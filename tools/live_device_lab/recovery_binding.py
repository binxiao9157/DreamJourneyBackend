"""Read-only remote identity gate for the newly created device scene only."""
import json
import subprocess
from pathlib import Path
from binding_contract import BindingError, require, validate_request


def remote_read(query):
    module = (Path(__file__).parent/'binding_contract.py').read_text()
    script = module + '\nq=' + repr(query) + '\n' + REMOTE_READ
    result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','dreamjourney-cloud',
        'sudo docker exec -i dreamjourneybackend-api-1 python -'], input=script, text=True,
        capture_output=True, timeout=25, check=True)
    return json.loads(result.stdout)


def approve_scene_source(device, remote, root, value, reader=remote_read, copier=None, expected_build=None):
    from lab import call, save, BUNDLE
    q = validate_request(value.get('sourceBindingRequest'))
    require(q['runID'] == value['runID'] and q['launchID'] == value['launchID'] and
            q['accountHash'] == value['accountHash'] and q['deviceID'] == device, 'bindingRuntimeIdentity')
    require(q['sessionID'] == value['capture']['recoverySessionID'] and
            q['productSessionID'] == value['capture']['productSessionID'], 'bindingRuntimeIdentity')
    from lab import execution_identity
    identity_path = root/'identity.json'
    if expected_build is None and identity_path.exists():
        expected_build = execution_identity(json.loads(identity_path.read_text()))
    require(expected_build is not None and q['buildIdentity'] == expected_build, 'bindingBuildIdentity')
    path = root/'scene-binding.json'
    # A file on disk is neither current server proof nor delivery acknowledgement.
    # Re-read the pinned revision (GET/SELECT only) before every transfer attempt.
    binding = reader(q)
    require(all(binding.get(k) == v for k,v in q.items()), 'bindingResponseIdentity')
    if binding.get('status') == 'ERROR':
        # Structured refusal reaches the phone immediately; never flatten it to a timeout.
        require(binding.get('code') in {'bindingSchema','bindingIdentity','bindingRevision','bindingMessages',
          'bindingMessageIdentity','bindingDuplicateMessage','bindingCanonicalIdentity','bindingRole','bindingHash',
          'bindingMissing','bindingSession','bindingAccount','bindingNotPublished','bindingSnapshotHash',
          'bindingPartialUnsupported','bindingSource','bindingMessageCount','bindingSequence','bindingDuplicateSource'}, 'bindingErrorCode')
    else:
        require(binding.get('status') == 'VERIFIED', 'bindingResponseStatus')
    save(path, binding)
    if copier:
        copier(path)
    else:
        call(['xcrun','devicectl','device','copy','to','--device',device,'--domain-type','appDataContainer',
              '--domain-identifier',BUNDLE,'--source',path,'--destination',remote+'/scene-binding.json'], timeout=25)
    return binding


REMOTE_READ = r'''
import os, psycopg
from psycopg.rows import dict_row
try:
 with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as connection:
  proof = read_binding(connection,q)
except BindingError as e:
 proof = dict(q,status='ERROR',code=e.code,readOnly=True)
print(json.dumps(proof))
'''
