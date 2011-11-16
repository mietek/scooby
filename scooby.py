#!/usr/bin/env python

# Copyright (C) 2011 Mietek Bak <mietek@varsztat.com>
# Distributed under the GNU GPL; see COPYING for more details.

import sys

if sys.version_info < (2, 7):
    print >> sys.stderr, "Error: Python 2.7 or newer required"
    sys.exit(1)

try:
    import eventlet
    eventlet.monkey_patch()
except ImportError:
    print >> sys.stderr, "Error: Eventlet required"
    sys.exit(1)

import argparse, csv, httplib, json, os, os.path, re, urllib2, zipfile


ARGS = None

def read_args():
    p = argparse.ArgumentParser(
        description="""A script for finding all the tracking scripts
            recognised by Ghostery used on each of the top 100,000 websites,
            as given by Alexa.  Results are written to stdout; status messages
            are written to stderr.""",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("-r", "--recache",
        help="recreate all the caches",
        action="store_true")
    p.add_argument("-q", "--quiet",
        help="do not show any status messages",
        action="store_true")
    p.add_argument("--max-connections",
        help="number of concurrent connections",
        default=100, type=int)
    p.add_argument("--max-sites",
        help="number of sites to process",
        default=1000, type=int)
    p.add_argument("--max-timeout",
        help="number of seconds per request",
        default=60, type=int)
    p.add_argument("--max-retries",
        help="number of retries per request",
        default=3, type=int)
    g = p.add_argument_group("arguments for overriding bugs-related defaults")
    g.add_argument("--bugs-url",
        help="location of the bugs list",
        default="http://www.ghostery.com/update/bugs?format=json")
    g.add_argument("--bugs-cache",
        help="path to local bugs list cache",
        default="/tmp/scooby_bugs.json")
    g = p.add_argument_group(
        "arguments for overriding sites-related defaults")
    g.add_argument("--sites-zip-url",
        help="location of the sites list archive",
        default="http://s3.amazonaws.com/alexa-static/top-1m.csv.zip")
    g.add_argument("--sites-zip-cache",
        help="path to local sites list archive cache",
        default="/tmp/scooby_all_sites.csv.zip")
    g.add_argument("--sites-csv-file",
        help="name of archived sites list file",
        default="top-1m.csv")
    g.add_argument("--sites-cache",
        help="path to local extracted sites list cache",
        default="/tmp/scooby_sites.csv")
    g.add_argument("--max-sites-cache-size",
        help="number of sites to extract",
        default=100000)
    global ARGS
    ARGS = p.parse_args()

def show_status(*items):
    if not ARGS.quiet:
        for item in items:
            print >> sys.stderr, item,
        print >> sys.stderr


class Bug:
    def __init__(self, bug_id, bug_name, bug_type, bug_pattern):
        self.id      = int(bug_id)
        self.name    = bug_name
        self.type    = bug_type
        self.pattern = bug_pattern

def download_bugs():
    if not os.path.exists(ARGS.bugs_cache) or ARGS.recache:
        show_status("Downloading bugs...")
        data = urllib2.urlopen(ARGS.bugs_url).read()
        with open(ARGS.bugs_cache, "w") as f:
            f.write(data)

def read_bugs():
    assert os.path.exists(ARGS.bugs_cache)
    show_status("Reading bugs...")
    with open(ARGS.bugs_cache, "r") as f:
        data = f.read()
    table = json.loads(data)["bugs"]
    bugs = []
    for row in table:
        try:
            bug_pattern = re.compile(row["pattern"])
        except:
            show_status("Ignored bug", row["id"] + ":", row["pattern"])
        else:
            bugs.append(Bug(row["id"], row["name"], row["type"], bug_pattern))
    bugs.sort(key=lambda bug: bug.id)
    show_status("Read", len(bugs), "bugs")
    return bugs


def download_sites():
    if not os.path.exists(ARGS.sites_zip_cache) or ARGS.recache:
        show_status("Downloading sites...")
        data = urllib2.urlopen(ARGS.sites_zip_url).read()
        with open(ARGS.sites_zip_cache, "wb") as f:
            f.write(data)

def extract_sites():
    assert os.path.exists(ARGS.sites_zip_cache)
    if not os.path.exists(ARGS.sites_cache) or ARGS.recache:
        show_status("Extracting sites...")
        rows = []
        with zipfile.ZipFile(ARGS.sites_zip_cache, "r") as z:
            with z.open(ARGS.sites_csv_file) as src:
                with open(ARGS.sites_cache, "w") as dst:
                    for i in range(0, ARGS.max_sites_cache_size):
                        dst.write(src.readline())

def read_sites():
    assert os.path.exists(ARGS.sites_cache)
    show_status("Reading sites...")
    sites = []
    with open(ARGS.sites_cache, "r") as f:
        table = csv.reader(f)
        for i in range(0, ARGS.max_sites):
            try:
                row = table.next()
            except StopIteration:
                break
            else:
                sites.append(row[1])
    show_status("Read", len(sites), "sites")
    return sites


def process_site(site, bugs, retries=0):
    show_status("Processing", site + "...")
    try:
        site_data = urllib2.urlopen("http://" + site,
            timeout=ARGS.max_timeout).read()
    except KeyboardInterrupt:
        raise
    except Exception as e:
        if (retries < ARGS.max_retries):
            show_status("Failure processing site", site + ":",
                str(e) + "; retrying...")
            return process_site(site, bugs, retries + 1)
        else:
            show_status("Failure processing site", site + ":", str(e))
            return {"site": site, "ok": False, "error": str(e)}
    else:
        bug_ids = []
        for bug in bugs:
            result = bug.pattern.search(site_data)
            if result != None:
                bug_ids.append(bug.id)
        show_status("Success processing site", site)
        return {"site": site, "ok": True, "bug_ids": bug_ids}


class Orderly:
    def __init__(self):
        self.total = 0
        self.processed = 0
        self.successes = 0

    def start(self, sites):
        self.total = len(sites)
        print "["

    def show_result(self, result):
        if self.processed == 0:
            print " ",
        else:
            print ",",
        self.processed += 1
        if result["ok"]:
            self.successes += 1
        print json.dumps(result)
        sys.stdout.flush()

    def stop(self):
        print "]"
        show_status("Processed", self.processed,
            "out of", self.total, "sites",
            "with", self.successes, "successes",
            "and", self.processed - self.successes, "failures")


def main():
    read_args()
    orderly = Orderly()
    try:
        download_bugs()
        bugs = read_bugs()
        download_sites()
        extract_sites()
        sites = read_sites()
        orderly.start(sites)
        pool = eventlet.GreenPool(ARGS.max_connections)
        for result in pool.imap(lambda site: process_site(site, bugs), sites):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        orderly.stop()

if __name__ == "__main__":
    main()
