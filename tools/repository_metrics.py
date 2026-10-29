"""Snapshot aggregate GitHub metrics locally; clones/downloads are not users."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess


def api(path):
    result=subprocess.run(['gh','api',path],capture_output=True,text=True,encoding='utf-8',timeout=30)
    if result.returncode:raise RuntimeError('GitHub query failed; verify gh authentication and repository permissions.')
    return json.loads(result.stdout)


def capture(repository):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise ValueError('Expected owner/repository')
    base='repos/'+repository;repo=api(base)
    if repo.get('private'):raise ValueError('This tool only reports public repositories')
    report={'schema':1,'repository':repository,'captured_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'stars':repo['stargazers_count'],'forks':repo['forks_count'],'limitations':[
            'Traffic is a rolling 14-day window; do not add successive snapshots together.',
            'Unique visitors and unique clone origins are GitHub aggregates, not identified people.',
            'Clones, release downloads and stars do not prove installations, active miners or retention.',
            'Maintainer activity and automation may contribute to these counts. No app telemetry is collected.']}
    for name in ('views','clones'):
        try:
            value=api(base+'/traffic/'+name)
            report[name]={'count':value['count'],'uniques':value['uniques'],'window_days':14,
                'daily':[{'timestamp':row['timestamp'],'count':row['count'],'uniques':row['uniques']} for row in value.get(name,[])]}
        except RuntimeError:report[name]={'unavailable':True,'reason':'GitHub traffic requires repository push access.'}
    releases=api(base+'/releases?per_page=100')
    report['release_downloads']=[{'tag':release['tag_name'],'prerelease':release['prerelease'],
        'assets':[{'name':asset['name'],'downloads':asset['download_count']} for asset in release.get('assets',[])]}
        for release in releases if not release.get('draft')]
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repository',default='TechFernandesLTDA/hashpilot')
    parser.add_argument('--output',type=Path);args=parser.parse_args()
    output=args.output or Path('dist/adoption-metrics')/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
    if output.exists():raise SystemExit('Output already exists; choose another path.')
    report=capture(args.repository);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as handle:json.dump(report,handle,ensure_ascii=False,indent=2)
    print(json.dumps({'saved':str(output),'stars':report['stars'],'forks':report['forks'],
        'views_14days':report['views'].get('count'),'unique_visitors_14days':report['views'].get('uniques'),
        'note':'Aggregate interest, not active-user counts.'}))


if __name__=='__main__':main()
