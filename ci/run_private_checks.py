"""Run private checks with public stage results and ephemeral private payloads."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SOURCE_REPOSITORY = 'dulanjy/CLSEN_Rag'
SENSITIVE_ENV = {'CLSEN_SOURCE_READ_KEY', 'GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_RUNTIME_TOKEN'}
COMMAND_ENV = {
    'PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'TEMP', 'TMP', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT',
    'LANG', 'LC_ALL', 'LANGUAGE', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'CURL_CA_BUNDLE',
    'REQUESTS_CA_BUNDLE', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG',
    'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'no_proxy', 'all_proxy',
}


class CheckFailure(Exception):
    def __init__(self, stage):
        self.stage = stage


def run(stage, command, *, cwd, environment, log):
    with log.open('ab') as output:
        result = subprocess.run(command, cwd=cwd, env=environment, stdout=output, stderr=output)
    if result.returncode:
        raise CheckFailure(stage)
    print(stage+' PASS', flush=True)


def counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else root.findall('testsuite')
    return {key: sum(int(suite.get(key, '0')) for suite in suites)
        for key in ('tests', 'failures', 'errors', 'skipped')}


def checkout(revision, root, environment):
    if re.fullmatch('[0-9a-f]{40}', revision) is None:
        raise CheckFailure('source_revision')
    key = environment.get('CLSEN_SOURCE_READ_KEY', '')
    if not key:
        raise CheckFailure('source_credential')
    source = root/'source'
    source.mkdir(mode=0o700)
    keyfile, hosts = root/'read-key', root/'known-hosts'
    try:
        keyfile.write_text(key.replace('\r\n', '\n').rstrip('\n')+'\n', encoding='utf-8', newline='\n')
        keyfile.chmod(0o600)
        meta = subprocess.run(['gh', 'api', 'meta'], env=environment, capture_output=True, check=False)
        if meta.returncode:
            raise CheckFailure('github_host_keys')
        keys = json.loads(meta.stdout)['ssh_keys']
        hosts.write_text(''.join('github.com '+value+'\n' for value in keys), encoding='utf-8')
        git_env = {name: value for name, value in environment.items() if name not in SENSITIVE_ENV}
        git_env['GIT_TERMINAL_PROMPT'] = '0'
        git_env['GIT_SSH_COMMAND'] = f'ssh -i "{keyfile}" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="{hosts}"'
        for arguments in [
            ['init', '--quiet'],
            ['remote', 'add', 'origin', 'git@github.com:'+SOURCE_REPOSITORY+'.git'],
            ['fetch', '--quiet', '--depth=1', '--no-tags', 'origin', revision],
            ['checkout', '--quiet', '--detach', 'FETCH_HEAD'],
        ]:
            result = subprocess.run(['git', *arguments], cwd=source, env=git_env, capture_output=True)
            if result.returncode:
                if b'error in libcrypto' in result.stderr or b'invalid format' in result.stderr:
                    raise CheckFailure('source_key_format')
                if b'Permission denied (publickey)' in result.stderr:
                    raise CheckFailure('source_key_access')
                raise CheckFailure('source_checkout')
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=source, capture_output=True, check=True)
        if head.stdout.decode().strip() != revision:
            raise CheckFailure('source_binding')
        return source
    finally:
        keyfile.unlink(missing_ok=True)
        hosts.unlink(missing_ok=True)


def selected_script(workflow, job, name):
    steps = workflow['jobs'][job]['steps']
    return next(step['run'] for step in steps if step.get('name') == name)


def command_environment(inherited):
    return {name: value for name, value in inherited.items() if name in COMMAND_ENV}


def check(source, revision, root, inherited):
    environment = command_environment(inherited)
    environment.update(
        CLSEN_RAG_TEST_OFFLINE_GUARD='1', CLSEN_RAG_TEST_POSTGRES_PRODUCT_INFORMATION='1',
        CLSEN_E4P1_PG_EVIDENCE_DIR=str(root/'postgres'),
        CLSEN_RAG_TEST_PRODUCT_IMAGE='clsen-product-knowledge:ci',
        SOURCE_REVISION=revision, GITHUB_REPOSITORY=SOURCE_REPOSITORY, CI='true',
    )
    (root/'postgres').mkdir()
    bootstrap = [sys.executable, '-m', 'pip', 'install', '--require-hashes',
        '-r', 'deploy/product-knowledge/requirements-linux.lock',
        '-r', 'deploy/product-knowledge/requirements-ci.lock']
    run('dependencies', bootstrap, cwd=source, environment=environment, log=root/'dependencies.log')
    import yaml
    workflow = yaml.safe_load((source/'.github/workflows/product-knowledge-ci.yml').read_text(encoding='utf-8'))
    commands = [
        ('frontend_dependencies', ['npm', '--prefix', 'frontend', 'ci']),
        ('frontend_types', ['npm', '--prefix', 'frontend', 'run', 'typecheck']),
        ('frontend_tests', ['npm', '--prefix', 'frontend', 'test', '--', '--reporter=junit', '--outputFile='+str(root/'frontend.xml')]),
        ('frontend_build', ['npm', '--prefix', 'frontend', 'run', 'build']),
        ('backend', ['bash', '-e', '-o', 'pipefail', '-c', selected_script(workflow, 'backend', 'Current product knowledge contracts')]),
        ('postgres_image', ['docker', 'pull', 'postgres:16-alpine']),
        ('postgres', ['bash', '-e', '-o', 'pipefail', '-c', selected_script(workflow, 'backend', 'PostgreSQL contracts and backup recovery')]),
        ('runtime_image', ['bash', '-e', '-o', 'pipefail', '-c', selected_script(workflow, 'image', 'Build runtime from verified frontend')]),
        ('runtime_checks', ['bash', '-e', '-o', 'pipefail', '-c', selected_script(workflow, 'image', 'Real image web and read-only MCP')]),
    ]
    for stage, command in commands:
        run(stage, command, cwd=source, environment=environment, log=root/(stage+'.log'))
    for stage, path in [('frontend', root/'frontend.xml'), ('backend', source/'ci-output/python.xml'),
                        ('postgres', source/'ci-output/postgres.xml'), ('image', source/'ci-output/image.xml')]:
        print(stage+' '+json.dumps(counts(path), sort_keys=True), flush=True)


def workspace():
    root = Path(os.environ['PRIVATE_CI_ROOT']).resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('clsen-private-ci-'):
        raise CheckFailure('private_workspace')
    return root


def main(mode):
    try:
        if mode == 'prepare':
            root = Path(tempfile.mkdtemp(prefix='clsen-private-ci-'))
            with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as target:
                target.write('PRIVATE_CI_ROOT='+str(root)+'\n')
            print('private_workspace PASS', flush=True)
            return 0
        if mode == 'cleanup':
            if os.environ.get('PRIVATE_CI_ROOT'):
                root = workspace()
                if root.exists():
                    shutil.rmtree(root)
            return 0
        root = workspace()
        revision = os.environ.get('SOURCE_REVISION', '')
        if mode == 'checkout':
            checkout(revision, root, os.environ)
            print('source_revision '+revision, flush=True)
        elif mode == 'check':
            if re.fullmatch('[0-9a-f]{40}', revision) is None:
                raise CheckFailure('source_revision')
            check(root/'source', revision, root, os.environ)
        else:
            raise CheckFailure('runner_mode')
        return 0
    except CheckFailure as error:
        print(error.stage+' FAIL', flush=True)
        return 1
    except Exception:
        print('runner_configuration FAIL', flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) == 2 else ''))
