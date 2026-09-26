"""One-shot cloud monitor. Only checkpoint JSON is public; plans stay on runner."""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import subprocess
import zipfile
from dataclasses import asdict
from pathlib import Path

from .alerts import AlertMessage, deliver
from .checker import Availability
from .config import Settings
from .monitor_state import initialize, load, observe, opaque, reserve, save, summary_period, utcnow
from .plush import ANIMALS, URL, check_animals

STATE = Path('monitor-state/state.json')
PLAN = Path('monitor-plan.json')


def gh(path: str, binary=False):
    result = subprocess.run(['gh', 'api', path], capture_output=True, check=True)
    return result.stdout if binary else json.loads(result.stdout)


def context(settings):
    key = os.environ.get('MONITOR_STATE_KEY', '')
    if len(key) < 32 or not settings.delivery_zip:
        raise ValueError('MONITOR_STATE_KEY and DELIVERY_ZIP are required')
    identity = opaque(key, json.dumps([URL, ANIMALS, settings.delivery_zip,
        settings.alert_email_to, settings.alert_sms_to, settings.summary_sms_to], sort_keys=True))
    return key, identity


def restore(identity, mode):
    if os.environ.get('GITHUB_RUN_ATTEMPT', '1') != '1':
        raise ValueError('Reruns are disabled; dispatch a new run to preserve checkpoint ordering')
    repo, run = os.environ['GITHUB_REPOSITORY'], int(os.environ['GITHUB_RUN_ID'])
    runs = gh(f'repos/{repo}/actions/workflows/monitor.yml/runs?per_page=100')['workflow_runs']
    previous = sorted((r for r in runs if r['id'] < run), key=lambda r: r['id'], reverse=True)
    if not previous:
        if mode != 'initialize':
            raise ValueError('No checkpoint: first run must explicitly initialize')
        return initialize(identity, utcnow())
    prior = previous[0]
    if prior['status'] != 'completed':
        raise ValueError('Prior monitor run is still active')
    artifacts = gh(f"repos/{repo}/actions/runs/{prior['id']}/artifacts")['artifacts']
    for name in ('monitor-final', 'monitor-intent'):
        matching = [a for a in artifacts if a['name'] == name]
        if not matching:
            continue
        if len(matching) != 1 or matching[0]['expired'] or matching[0]['size_in_bytes'] > 32768:
            raise ValueError('Latest checkpoint expired or invalid; no older fallback permitted')
        raw = gh(f"repos/{repo}/actions/artifacts/{matching[0]['id']}/zip", binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if archive.namelist() != ['state.json'] or archive.getinfo('state.json').file_size > 16384:
                raise ValueError('Unexpected checkpoint archive')
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_bytes(archive.read('state.json'))
        return load(STATE, identity)
    raise ValueError('Latest run has no checkpoint; explicit recovery required, never revert to older state')


def channels(settings, secondary=False):
    result = []
    if settings.email_enabled and not secondary:
        result.append(('email', settings.alert_email_to))
    recipients = [settings.summary_sms_to] if secondary and settings.summary_sms_to else ([] if secondary else settings.sms_recipients)
    channel = 'sms' if settings.sms_enabled else ('textbelt' if settings.textbelt_enabled else None)
    if channel:
        result.extend((channel, r) for r in recipients)
    return result


def message_for(results, settings, *, test=False):
    first = next(iter(results))
    subject = (f'TEST — {first} availability check — not a Capybara restock.' if test else
               ('Restock: Jumbo Baby Animal Plush - Capybara' if len(results) == 1 else 'Jumbo Baby Animal Plush stock summary'))
    lines = [subject, f'Delivery ZIP: {settings.delivery_zip}; US standard shipping']
    for animal, result in results.items():
        lines.append(f'{animal} ({ANIMALS[animal]}): {result.status}; {result.checked_at}' + (f'; {result.price}' if result.price else ''))
    body = '\n'.join([*lines, URL])
    sms = '\n'.join(lines)
    if settings.sms_include_url:
        sms += '\n' + URL
    return AlertMessage(subject, body, sms)


def prepare(settings, state, key, mode, checker=check_animals):
    now = utcnow()
    primary_period = summary_period(state, now, 2)
    secondary_period = summary_period(state, now, 14)
    animals = list(ANIMALS) if primary_period or secondary_period else ['Capybara']
    if mode in {'verify', 'test'}:
        animals = ['Capybara', 'Dog', 'Red Panda']
    results = checker(settings, animals)
    # Control tests never alter the production baseline or summary state.
    if mode != 'test':
        observe(state, results['Capybara'].status)
    jobs = []

    def queue(slot, token, readings, secondary=False, test=False):
        event_id = opaque(key, f'{slot}:{token}')
        for channel, recipient in channels(settings, secondary):
            recipient_key = opaque(key, f'{channel}:{recipient}')
            budget = ('email' if channel == 'email' else 'sms') if test else None
            if reserve(state, slot=slot, event_id=event_id, recipient_key=recipient_key, test_channel=budget):
                jobs.append(dict(slot=slot, event_id=event_id, recipient_key=recipient_key,
                    channel=channel, recipient=recipient, message=asdict(message_for(readings, settings, test=test))))

    if mode == 'test':
        control = next((a for a in animals if a != 'Capybara' and results[a].availability == Availability.IN_STOCK), None)
        if control:
            # Fixed token across invocations prevents a second control animal from sending again.
            queue('test', 'initial-control-test', {control: results[control]}, test=True)
    elif mode != 'verify':
        if results['Capybara'].status == 'in_stock':
            queue('restock', str(state['epoch']), {'Capybara': results['Capybara']})
        if primary_period:
            queue('summary', primary_period, results)
        if secondary_period:
            queue('secondary', secondary_period, results, secondary=True)
    return results, jobs


def send_jobs(settings, state, jobs, sender=deliver, checkpoint=lambda state: None):
    for job in jobs:
        event = state['events'][job['slot']]
        record = event['deliveries'][job['recipient_key']]
        if event['id'] != job['event_id'] or record['status'] != 'pending':
            raise ValueError('Notification plan does not match checkpoint')
        record['status'] = sender(settings, job['channel'], job['recipient'], AlertMessage(**job['message']),
                                   job['event_id'] + '-' + job['recipient_key'])
        checkpoint(state)


def report(results, jobs, state):
    lines = ['## Monitor check', '', f'Checked at {utcnow().isoformat()}',
             'Delivery: configured private ZIP, US standard shipping.', '',
             '| Variant | SKU | Result | Evidence |', '|---|---|---|---|']
    for animal, result in results.items():
        lines.append(f'| {animal} | {ANIMALS[animal]} | {result.status} | {result.detail} |')
    lines += ['', f'Notification attempts reserved: {len(jobs)}.',
              'Provider acceptance is not confirmed delivery. Private settings and session data are omitted.']
    output = '\n'.join(lines) + '\n'
    print(output)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'send'])
    parser.add_argument('--mode', choices=['monitor', 'initialize', 'verify', 'test'], default='monitor')
    args = parser.parse_args()
    settings = Settings(enable_scheduler=False)
    key, identity = context(settings)
    with open('.monitor.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == 'prepare':
            state = restore(identity, args.mode)
            results, jobs = prepare(settings, state, key, args.mode)
            save(STATE, state)
            PLAN.write_text(json.dumps(jobs))
            PLAN.chmod(0o600)
            report(results, jobs, state)
        else:
            state = load(STATE, identity)
            send_jobs(settings, state, json.loads(PLAN.read_text()), checkpoint=lambda s: save(STATE, s))
            outcomes = [d['status'] for e in state['events'].values() for d in e['deliveries'].values()]
            summary = 'Notification outcomes: ' + (', '.join(outcomes) or 'no attempts') + '\n'
            print(summary)
            if os.environ.get('GITHUB_STEP_SUMMARY'):
                with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
                    stream.write(summary)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Exceptions from HTTP libraries may contain credentials or private URLs.
        print(f'Monitor stopped safely ({type(exc).__name__}). Check checkpoint and required configuration.')
        raise SystemExit(1) from None
