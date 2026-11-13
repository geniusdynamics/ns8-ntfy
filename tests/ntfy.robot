*** Settings ***
Library    SSHLibrary
Library    String

*** Variables ***
${HOST}                  ntfy.example.org
${SCENARIO}              install
# The update scenario starts from the current stable release, which keeps
# its settings in NTFY_* variables and its data in the state directory.
${PREVIOUS_IMAGE_URL}    ghcr.io/geniusdynamics/ntfy:1.0.0
${module_id}             ${EMPTY}
${web_port}              ${EMPTY}
${ADMIN_USER}            admin
${ADMIN_PASSWORD}        Nethesis,1234

*** Keywords ***
Login to cluster-admin
    New Page    https://${NODE_ADDR}/cluster-admin/
    Fill Text    text="Username"    ${ADMIN_USER}
    Click    button >> text="Continue"
    Fill Text    text="Password"    ${ADMIN_PASSWORD}
    Click    button >> text="Log in"
    Wait For Elements State    css=#main-content    visible    timeout=10s

ntfy health endpoint is reachable
    ${output}    ${rc} =    Execute Command    curl -fsS --max-time 5 http://127.0.0.1:${web_port}/v1/health
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0
    Should Contain    ${output}    "healthy":true

Wait until ntfy is healthy
    Wait Until Keyword Succeeds    120 seconds    2 seconds    ntfy health endpoint is reachable

Read allocated web port
    ${port} =    Execute Command    runagent -m ${module_id} printenv TCP_PORT
    ${port} =    Strip String    ${port}
    Should Match Regexp    ${port}    ^[0-9]+$
    Set Suite Variable    ${web_port}    ${port}

*** Test Cases ***
Check if ntfy is installed correctly
    IF    r'${SCENARIO}' == 'update'
        Set Local Variable    ${install_image}    ${PREVIOUS_IMAGE_URL}
    ELSE
        Set Local Variable    ${install_image}    ${IMAGE_URL}
    END
    ${output}  ${rc} =    Execute Command    add-module ${install_image} 1
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0    add-module ${install_image} failed: ${output}
    &{output} =    Evaluate    ${output}
    Set Suite Variable    ${module_id}    ${output.module_id}

Check if ntfy can be configured
    # The same request as the Settings page of every release
    ${output}  ${rc} =    Execute Command    api-cli run module/${module_id}/configure-module --data '{"host":"${HOST}","http2https":true,"lets_encrypt":false}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}  0    configure-module failed: ${output}

Update module from the previous release
    Skip If    r'${SCENARIO}' != 'update'    only the update scenario updates the module
    ${output}    ${rc} =    Execute Command    api-cli run update-module --data '{"force":true,"module_url":"${IMAGE_URL}","instances":["${module_id}"]}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    update-module ${IMAGE_URL} failed: ${output}
    ${journal} =    Execute Command    journalctl -q --no-pager SYSLOG_IDENTIFIER=agent@${module_id}
    Should Not Contain    ${journal}    has failed    an update-module.d step failed
    # The NTFY_* settings moved to server.yml: they would override it
    ${environment} =    Execute Command    runagent -m ${module_id} cat environment
    Should Not Contain    ${environment}    NTFY_BASE_URL
    Should Not Contain    ${environment}    NTFY_AUTH_DEFAULT_ACCESS
    Should Not Contain    ${environment}    NTFY_WEB_PUSH_PRIVATE_KEY

Check the configuration is read back
    ${output}    ${rc} =    Execute Command    api-cli run module/${module_id}/get-configuration
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0
    &{config} =    Evaluate    json.loads(r'''${output}''')    modules=json
    Should Be Equal    ${config.host}    ${HOST}
    Should Be True    ${config.http2https}
    Should Contain    ${config.server_config}    base-url
    Should Not Contain    ${output}    upstream-access-token
    Should Not Contain    ${output}    private

Check if ntfy works as expected
    ${rc} =    Execute Command    bash -c 'for i in $(seq 1 60); do curl -k -s -o /dev/null -f --resolve ${HOST}:443:127.0.0.1 https://${HOST}/ && exit 0; sleep 5; done; exit 1'
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
    Read allocated web port
    Wait until ntfy is healthy

Check packaged runtime version
    ${output}    ${rc} =    Execute Command    runagent -m ${module_id} podman exec ntfy-app ntfy --version
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0
    ${running_image} =    Execute Command    runagent -m ${module_id} podman inspect --format '{{.ImageName}}' ntfy-app
    ${running_image} =    Strip String    ${running_image}
    Should Match Regexp    ${running_image}    ^docker[.]io/binwiederhier/ntfy:v[0-9]+[.][0-9]+[.][0-9]+$
    ${tag} =    Fetch From Right    ${running_image}    :v
    Should Contain    ${output}    ${tag}

Check server.yml persistence and read-only mount
    ${mode} =    Execute Command    runagent -m ${module_id} stat -c '%a' config/server.yml
    ${mode} =    Strip String    ${mode}
    Should Be Equal    ${mode}    600
    ${read_write} =    Execute Command    runagent -m ${module_id} podman inspect ntfy-app --format '{{range .Mounts}}{{if eq .Destination "/etc/ntfy"}}{{println .RW}}{{end}}{{end}}'
    ${read_write} =    Strip String    ${read_write}
    Should Be Equal    ${read_write}    false
    # The state directory holds the agent credentials: it must not be mounted
    ${volume} =    Execute Command    runagent -m ${module_id} podman inspect ntfy-app --format '{{range .Mounts}}{{if eq .Destination "/var/lib/ntfy"}}{{println .Name}}{{end}}{{end}}'
    ${volume} =    Strip String    ${volume}
    Should Be Equal    ${volume}    ntfy-app

Check enforced NS8 proxy settings
    ${environment} =    Execute Command    runagent -m ${module_id} podman exec ntfy-app env
    Should Contain    ${environment}    NTFY_BEHIND_PROXY=true
    Should Contain    ${environment}    NTFY_PROXY_FORWARDED_HEADER=X-Forwarded-For
    Should Contain    ${environment}    NTFY_LISTEN_HTTP=:80
    Should Not Contain    ${environment}    NTFY_AUTH_DEFAULT_ACCESS=

Reject duplicate and module-managed YAML options
    ${payload} =    Evaluate    json.dumps({"host": $HOST, "http2https": True, "lets_encrypt": False, "server_config": "{enable-login: true, enable-login: false}"})    modules=json
    ${output}    ${rc} =    Execute Command    api-cli run module/${module_id}/configure-module --data '${payload}'
    ...    return_rc=True
    Should Not Be Equal As Integers    ${rc}    0
    ${payload} =    Evaluate    json.dumps({"host": $HOST, "http2https": True, "lets_encrypt": False, "server_config": "behind-proxy: false"})    modules=json
    ${output}    ${rc} =    Execute Command    api-cli run module/${module_id}/configure-module --data '${payload}'
    ...    return_rc=True
    Should Not Be Equal As Integers    ${rc}    0
    Wait until ntfy is healthy

Configure ntfy with an edited server.yml
    # Anonymous read-write access, so the next tests can publish without a token
    ${server_config} =    Catenate    SEPARATOR=\n
    ...    base-url: "https://${HOST}"
    ...    cache-file: "/var/lib/ntfy/cache.db"
    ...    attachment-cache-dir: "/var/lib/ntfy/attachments"
    ...    auth-file: "/var/lib/ntfy/auth.db"
    ...    auth-default-access: "read-write"
    ${payload} =    Evaluate    json.dumps({"host": $HOST, "http2https": True, "lets_encrypt": False, "server_config": $server_config})    modules=json
    ${output}    ${rc} =    Execute Command    api-cli run module/${module_id}/configure-module --data '${payload}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    configure-module failed: ${output}
    Wait until ntfy is healthy
    ${config} =    Execute Command    runagent -m ${module_id} cat config/server.yml
    Should Match Regexp    ${config}    auth-default-access: "?read-write

Manage accounts through NS8 actions
    # Do not record generated credentials in Robot's command output.
    ${rc} =    Execute Command    api-cli run module/${module_id}/manage-account --data '{"operation":"add-user","username":"ci-worker"}' >/dev/null
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    Wait until ntfy is healthy
    ${rc} =    Execute Command    api-cli run module/${module_id}/manage-account --data '{"operation":"reset-password","username":"ci-worker"}' >/dev/null
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    Wait until ntfy is healthy
    ${rc} =    Execute Command    api-cli run module/${module_id}/manage-account --data '{"operation":"add-token","username":"ci-worker","label":"ci-monitoring","expires":"1d"}' >/dev/null
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    Wait until ntfy is healthy
    ${rc} =    Execute Command    api-cli run module/${module_id}/manage-account --data '{"operation":"set-access","username":"ci-worker","topic":"ci-alerts","permission":"write-only"}' >/dev/null
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    Wait until ntfy is healthy
    ${output}    ${rc} =    Execute Command    api-cli run module/${module_id}/list-accounts
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0
    Should Contain    ${output}    ci-worker
    Should Contain    ${output}    ci-monitoring
    Should Contain    ${output}    ci-alerts
    Should Not Contain    ${output}    tk_
    Should Not Contain    ${output}    "password"

Publish and subscribe through Traefik
    ${topic} =    Set Variable    ci-${SCENARIO}
    ${message} =    Set Variable    qemu-${SCENARIO}-message
    ${output}    ${rc} =    Execute Command    curl -k -fsS --max-time 10 --resolve ${HOST}:443:127.0.0.1 -d '${message}' https://${HOST}/${topic}
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    publish failed: ${output}
    ${output}    ${rc} =    Execute Command    curl -k -fsS --max-time 10 --resolve ${HOST}:443:127.0.0.1 'https://${HOST}/${topic}/json?poll=1'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    subscribe failed: ${output}
    Should Contain    ${output}    "message":"${message}"

Check ntfy data is stored in the backed up volume
    ${rc} =    Execute Command    runagent -m ${module_id} sh -c 'test -f "$(podman volume inspect --format "{{.Mountpoint}}" ntfy-app)/cache.db"'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    cache.db is not in the ntfy-app volume
    ${rc} =    Execute Command    runagent -m ${module_id} test ! -e cache.db
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    cache.db is still in the state directory

Check backup snapshot and service restart
    ${rc} =    Execute Command    runagent -m ${module_id} module-dump-state
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    ntfy snapshot failed
    ${snapshot} =    Execute Command    runagent -m ${module_id} podman unshare sh -c 'cat "$(podman volume inspect --format "{{.Mountpoint}}" ntfy-app)/.ntfy-backup-snapshot/server.yml"'
    Should Contain    ${snapshot}    cache-file: "/var/lib/ntfy/cache.db"
    ${rc} =    Execute Command    runagent -m ${module_id} podman unshare sh -c 'test -f "$(podman volume inspect --format "{{.Mountpoint}}" ntfy-app)/.ntfy-backup-snapshot/data/auth.db"'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    snapshot has no auth database
    Wait until ntfy is healthy
    ${rc} =    Execute Command    runagent -m ${module_id} module-cleanup-state
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    snapshot cleanup failed
    ${rc} =    Execute Command    runagent -m ${module_id} podman unshare sh -c 'test ! -e "$(podman volume inspect --format "{{.Mountpoint}}" ntfy-app)/.ntfy-backup-snapshot"'
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    temporary snapshot remains after cleanup

Module UI loads in cluster-admin
    [Tags]    ui
    Import Library    Browser
    New Browser    chromium    headless=True
    New Context    ignoreHTTPSErrors=True
    Login to cluster-admin
    Go To    https://${NODE_ADDR}/cluster-admin/#/apps/${module_id}?page=settings
    Wait For Elements State    iframe >>> h2 >> text="Settings"    visible    timeout=20s
    # The host field is only enabled and filled after get-configuration
    Wait For Elements State    iframe >>> input[placeholder="ntfy.example.org"]    enabled    timeout=30s
    Get Property    iframe >>> input[placeholder="ntfy.example.org"]    value    ==    ${HOST}
    Take Screenshot    filename=${OUTPUT DIR}/browser/screenshot/1._Settings.png
    Go To    https://${NODE_ADDR}/cluster-admin/#/apps/${module_id}?page=accounts
    Wait For Elements State    iframe >>> h2 >> text="Users and tokens"    visible    timeout=20s
    Wait For Elements State    iframe >>> td >> text="ci-worker" >> nth=0    visible    timeout=30s
    Get Property    iframe >>> select#token-expiry    value    ==    never
    Take Screenshot    filename=${OUTPUT DIR}/browser/screenshot/2._Accounts.png
    Close Browser

Check if ntfy is removed correctly
    ${rc} =    Execute Command    remove-module --no-preserve ${module_id}
    ...    return_rc=True  return_stdout=False
    Should Be Equal As Integers    ${rc}  0
