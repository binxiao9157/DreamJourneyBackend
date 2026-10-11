"""Pure read-only noChange proof. Capture account metadata around the actual UI attempt."""

def validate(before, after, observation):
    assert observation['status']=='NO_CHANGE_OBSERVED', 'device terminal'
    assert before['accountHash']==after['accountHash']==observation['accountHash'], 'account identity'
    assert after['sessionID']==observation['capture']['recoverySessionID'], 'session identity'
    assert after['productSessionID']==observation['capture']['productSessionID'], 'product identity'
    assert after['sourceID']==observation['sourceID'], 'source identity'
    assert after['state']=='noChange', 'not noChange'
    assert after['frozen'] and after['finalSequence']==after['continuousSequence'], 'incomplete source'
    assert after['formal']==before['formal'], 'formal identity/version/content changed'
    assert after['candidates']==before['candidates'], 'candidate set or content changed'
    return dict(status='PASS', scope='actual noChange with exact candidate and formal identities, versions and hashes', formalCount=len(after['formal']), candidateCount=len(after['candidates']))
