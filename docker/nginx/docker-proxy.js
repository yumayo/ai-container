var fs = require('fs');

var allowList = [];

function loadAllowList() {
  try {
    var data = fs.readFileSync('/etc/nginx/docker-proxy-allow.txt', 'utf8');
    var trimmed = data.trim();
    if (trimmed.length === 0) {
      allowList = [];
      return;
    }
    allowList = trimmed.split(',').map(function(s) { return s.trim(); }).filter(function(s) { return s.length > 0; });
  } catch (e) {
    allowList = [];
  }
}

loadAllowList();

function basename(path) {
  var parts = path.split('/');
  return parts[parts.length - 1];
}

function exec_filter(r) {
  if (allowList.length === 0) {
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

  var executable = basename(cmd[0]);

  var allowed = false;
  for (var i = 0; i < allowList.length; i++) {
    if (allowList[i] === executable) {
      allowed = true;
      break;
    }
  }

  if (allowed) {
    r.internalRedirect('@docker_backend');
  } else {
    r.return(403, JSON.stringify({message: 'Forbidden: command "' + executable + '" is not allowed. Allowed commands: ' + allowList.join(', ')}));
  }
}

export default { exec_filter };
