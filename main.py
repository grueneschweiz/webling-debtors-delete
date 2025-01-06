import math
import os
import sys
import urllib.parse
from time import sleep

import requests
import argparse
from datetime import datetime
from dotenv import load_dotenv
from requests import ReadTimeout

load_dotenv()
api_key = os.getenv('API_KEY')
api_url = os.getenv('API_URL')
headers = {'apikey': api_key}

request_timeout = 120  # seconds
retry_attempts = 2


def get(url):
    response = requests.get(headers=headers, url=url, timeout=request_timeout)
    response.raise_for_status()
    return response.json()


def delete(url):
    response = requests.delete(headers=headers, url=url, timeout=request_timeout)
    return response


def get_api_url(endpoint, params=''):
    endpoint = '/' + endpoint.lstrip('/')

    if params:
        params = params.lstrip('?')
        params = '?' + urllib.parse.quote(params, safe='&=')

    return api_url + endpoint + params


def get_open_debtor_ids(period_id: int, filter_titles: [str]) -> [int]:
    # get open debtors of given period only
    filter_params = [
        'state = "open"',
        '$parents.$id = ' + str(period_id)
    ]

    # filter by title
    if filter_titles:
        filter_params.append('`title` IN ("' + '", "'.join(filter_titles) + '")')

    params = 'filter=' + ' AND '.join(filter_params)

    try:
        resp = get(get_api_url('debitor', params))
    except requests.exceptions.HTTPError as e:
        if 503 == e.response.status_code:
            print(e, file=sys.stderr, flush=True)
            sys.exit(1)
        else:
            raise e

    if resp['objects']:
        return resp['objects']

    return []


def get_period_name(period_id: int):
    period = get(get_api_url('period/' + str(period_id)))
    return period['properties']['title']


def get_accounting_name(period_id: int):
    params = 'format=full&filter=$children.period.$id = ' + str(period_id)

    accountings = get(get_api_url('periodgroup', params))
    return accountings[0]['properties']['title']


def delete_debtors(dry_run: bool, debtors: str):
    global retry_attempts

    if dry_run:
        return True

    # retry if request times out, or we don't get the expected response status code
    for i in range(0, retry_attempts):
        try:
            resp = delete(get_api_url('debitor/' + debtors))
            if 204 == resp.status_code:
                return True
            else:
                i += 1
        except ReadTimeout:
            i += 1

    return False


def get_eta(current: int, count: int) -> str:
    global process_start_time

    if current <= 0:
        return 'Infinity'

    running_duration = datetime.now() - process_start_time
    estimated_duration = (count / current) * running_duration
    eta = estimated_duration - running_duration

    eta_str = str(eta)

    if ',' in eta_str:
        eta_str = eta_str.split(',')[0]

    if '.' in eta_str:
        eta_str = eta_str.split('.')[0]

    return eta_str


def run(dry_run: bool, period_id: int, batch_size: int, titles: [str], ignore_titles: bool):
    global process_start_time

    if ignore_titles:
        title_info = ''
        all_info = 'all '
        titles = []

    else:
        title_info = ' with title "' + '" or "'.join(titles) + '"'
        all_info = ''

    # give the user a possibility to verify the period id and abort if needed
    period_name = get_period_name(period_id)
    accounting_name = get_accounting_name(period_id)
    print(f'Deleting {all_info}open debtors for period "{period_name}" in "{accounting_name}"{title_info}', file=sys.stderr, flush=True)
    print('Press Ctrl+C to abort', file=sys.stderr, flush=True)
    sleep(10)

    # get ids of open debtors. This will take a while
    print('Fetching ids of relevant debtors. This will take a while.', file=sys.stderr, flush=True)
    debtor_ids = get_open_debtor_ids(period_id, titles)
    debtor_count = len(debtor_ids)

    print(f'Found {debtor_count} open debtors{title_info}', file=sys.stderr, flush=True)

    error_count = 0

    # chunk debtor ids into blocks
    debtor_id_blocks = [debtor_ids[i:i + batch_size] for i in range(0, debtor_count, batch_size)]

    if len(debtor_id_blocks) == 0:
        print('No debtor blocks to process.', file=sys.stderr, flush=True)
        return

    # setup logging
    block_digits = math.ceil(math.log10(len(debtor_id_blocks)))
    block_count = len(debtor_id_blocks)
    log_template = '[ETA {eta}  ' \
                   '{current:' + str(block_digits) + 'd}' \
                   '/{total:' + str(block_digits) + 'd} ' \
                   'Blocks à {block_size:d} debtors]'

    # reset time measurement for eta
    process_start_time = datetime.now()

    for block_num, debtor_id_block in enumerate(debtor_id_blocks):
        debtors = ','.join(map(str, debtor_id_block))
        block_size = len(debtor_id_block)

        print(
            log_template.format(
                eta=get_eta(block_num, block_count),
                current=block_num + 1,
                total=block_count,
                block_size=block_size,
                count=debtor_count,
                ids=debtors),
            end='')

        if dry_run:
            print('  DRY RUN', end='')

        if delete_debtors(dry_run, debtors):
            print('  SUCCESS  ids: ' + debtors, end='\n')
        else:
            error_count += 1
            print('  ERROR  ids: ' + debtors, end='\n')

    if error_count > 0:
        print(f'All done, but with {error_count} batches failed to delete.', file=sys.stderr, flush=True)
        print('Rerun program.', file=sys.stderr, flush=True)
    else:
        print('All done.', file=sys.stderr, flush=True)


if not api_url or not api_key:
    print('API_URL and API_KEY environment variables are required', file=sys.stderr, flush=True)
    sys.exit(1)

parser = argparse.ArgumentParser(
    description='Delete open debtors of a given period in Webling',
    epilog='See https://github.com/grueneschweiz/webling-debtors-delete'
)
parser.add_argument('period_id', type=int, help='Id of the accounting period to delete debtors from.')
parser.add_argument('--batch-size', action='store', help="Batch size for deletion. Default: 100", type=int, default=100)
parser.add_argument('--title', action='append', help="Title text of the debtors to delete. Required unless --all is set. Repeat for multiple titles.", type=str, default=[])
parser.add_argument('--all', action='store_true', help="Delete all open debtors. Mutually exclusive with --title.")
parser.add_argument('--dry-run', action='store_true', help="Don't apply changes to Webling", default=False)
args = parser.parse_args()

if args.all and args.title:
    print('Options --all and --title are mutually exclusive.', file=sys.stderr, flush=True)
    sys.exit(1)

if not args.all and not args.title:
    print('Either --all or --title is required.', file=sys.stderr, flush=True)
    sys.exit(1)

process_start_time = datetime.now()

run(args.dry_run, args.period_id, args.batch_size, args.title, args.all)
