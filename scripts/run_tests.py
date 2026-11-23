"""Tests reset only the dedicated disposable test database."""
import os
from pathlib import Path
import subprocess
import sys
from dotenv import load_dotenv

root=Path(__file__).resolve().parents[1]
load_dotenv(root/'.env')
env=os.environ.copy()
env['DATABASE_URL']=os.environ['TEST_DATABASE_URL']
env['ADMIN_DATABASE_URL']=os.environ['ADMIN_TEST_DATABASE_URL']
if 'resolvelab_test' not in env['ADMIN_DATABASE_URL'].rsplit('/',1)[-1]:
    raise SystemExit('Test reset is restricted to the dedicated resolvelab_test database.')
from resolvelab.isolation import validate_disposable_urls
validate_disposable_urls(env['DATABASE_URL'],env['ADMIN_DATABASE_URL'],'resolvelab_test')
raise SystemExit(subprocess.call([sys.executable,'-m','pytest',*sys.argv[1:]],cwd=root,env=env))
