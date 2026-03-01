var fs = require('fs');

var allowRules = [];

function loadAllowList() {
  try {
    var data = fs.readFileSync('/etc/nginx/docker-proxy-allow.txt', 'utf8');
    var trimmed = data.trim();
    if (trimmed.length === 0) {
      allowRules = [];
      return;
    }
    allowRules = trimmed.split(',').map(function(s) {
      return s.trim().split(/\s+/);
    }).filter(function(parts) {
      return parts.length > 0 && parts[0].length > 0;
    });
  } catch (e) {
    allowRules = [];
  }
}

loadAllowList();

function basename(path) {
  var parts = path.split('/');
  return parts[parts.length - 1];
}

function matchRule(rule, cmd) {
  for (var i = 0; i < rule.length; i++) {
    if (i >= cmd.length) {
      return false;
    }
    var expected = rule[i];
    var actual = (i === 0) ? basename(cmd[i]) : cmd[i];
    if (expected !== actual) {
      return false;
    }
  }
  return true;
}

function exec_filter(r) {
  if (allowRules.length === 0) {
    r.return(403, JSON.stringify({message: 'Forbidden: no commands are allowed (docker-proxy-allow is not configured)'}));
    return;
  }

  var body;
  try {
    body = JSON.parse(r.requestText);
  } catch (e) {
    r.return(400, JSON.stringify({message: 'Bad Request: invalid JSON body'}));
    return;
  }

  var cmd = body.Cmd;
  if (!cmd || !Array.isArray(cmd) || cmd.length === 0) {
    r.return(400, JSON.stringify({message: 'Bad Request: missing Cmd'}));
    return;
  }

  var allowed = false;
  for (var i = 0; i < allowRules.length; i++) {
    if (matchRule(allowRules[i], cmd)) {
      allowed = true;
      break;
    }
  }

  if (allowed) {
    r.internalRedirect('@docker_backend');
  } else {
    var cmdStr = basename(cmd[0]) + (cmd.length > 1 ? ' ' + cmd.slice(1).join(' ') : '');
    var ruleStrs = allowRules.map(function(r) { return r.join(' '); });
    r.return(403, JSON.stringify({message: 'Forbidden: command "' + cmdStr + '" is not allowed. Allowed commands: ' + ruleStrs.join(', ')}));
  }
}

export default { exec_filter };
