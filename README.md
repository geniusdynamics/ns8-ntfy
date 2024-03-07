# ns8-ntfy

THis is a NEthserver 8 App for NTFY(notify) [NTFY](https://github.com/binwiederhier/ntfy) 


## Install

Instantiate the module with:

    add-module ghcr.io/geniusdynamics/ntfy:latest 1

The output of the command will return the instance name.
Output example:

    {"module_id": "ntfy1", "image_name": "ntfy", "image_url": "ghcr.io/geniusdynamics/ntfy:latest"}

## Configure

Let's assume that the mattermost instance is named `ntfy1`.

Launch `configure-module`, by setting the following parameters:
- `host`: a fully qualified domain name for the application
- `http2https`: enable or disable HTTP to HTTPS redirection (true/false)
- `lets_encrypt`: enable or disable Let's Encrypt certificate (true/false)
- `NTFY_BASE_URL`: Public facing base URL of the service (e.g. https://ntfy.domain.sh)
- `NTFY_AUTH_DEFAULT_ACCESSt`: Default permissions if no matching entries in the auth database are found. Default is read-write. read-write, read-only, write-only, deny-all
- `NTFY_BEHIND_PROXY`: enable or disable default true (true/false)
- `NTFY_ENABLE_LOGIN`: enable or disable default false (true/false)
- `NTFY_ENABLE_SIGNUP`: enable or disable default false (true/false)
- `NTFY_UPSTREAM_BASE_URL`: Forward poll request to an upstream server, this is needed for iOS push notifications for self-hosted servers https://ntfy.sh
- `NTFY_UPSTREAM_ACCESS_TOKEN`: Access token to use for the upstream server; needed only if upstream rate limits are exceeded or upstream server requires auth
- `NTFY_WEB_PUSH_EMAIL_ADDRESS`: Web Push: Sender email address
- `NTFY_CACHE_FILE`: If set, messages are cached in a local SQLite database instead of only in-memory. This allows for service restarts without losing messages in support of the since= parameter
- `NTFY_ATTACHMENT_CACHE_DIR`: enable or disable Let's Encrypt certificate (true/false)
- `NTFY_AUTH_FILE`: Auth database file used for access control. If set, enables authentication and access control. See access control.
- `NTFY_WEB_PUSH_FILE`: Web Push: Database file that stores subscriptions
- `NTFY_WEB_PUSH_PUBLIC_KEY`: Web Push: Public Key. Run ntfy webpush keys to generate
- `NTFY_WEB_PUSH_PRIVATE_KEY`: Web Push: Private Key. Run ntfy webpush keys to generate

[More Variables](https://docs.ntfy.sh/config/#config-options)
Example:

```
api-cli run configure-module --agent module/ntfy1 --data - <<EOF
{
  "host": "ntfy.domain.com",
  "http2https": true,
  "lets_encrypt": false
}
EOF
```

The above command will:
- start and configure the ntfy instance
- configure a virtual host for trafik to access the instance

## Get the configuration
You can retrieve the configuration with

```
api-cli run get-configuration --agent module/ntfy1
```
## Web Push Private and Public key Pairs
- enter into the container

`ssh ntfy1@localhost`

- run the command inside the container 
 `podman exec ntfy-app  ntfy webpush keys`
 ```
Web Push keys generated. Add the following lines to your config file:

web-push-public-key: BIoV3b7JhU0y-4CeP32PmFcVTQB5_rAfC99S8684FI72pC50GvICMwmTn1TLcqqbiREcYLmgQVMvTRDS75Bpg_E
web-push-private-key: BrOm7ZuMouXzV8lT8xoC2wCSa7wscaZ9_JN3oKQama8
web-push-file: /var/cache/ntfy/webpush.db # or similar
web-push-email-address: <email address>

```
## Uninstall

To uninstall the instance:

    remove-module --no-preserve ntfy1

## Smarthost setting discovery

Some configuration settings, like the smarthost setup, are not part of the
`configure-module` action input: they are discovered by looking at some
Redis keys.  To ensure the module is always up-to-date with the
centralized [smarthost
setup](https://nethserver.github.io/ns8-core/core/smarthost/) every time
ntfy starts, the command `bin/discover-smarthost` runs and refreshes
the `state/smarthost.env` file with fresh values from Redis.

Furthermore if smarthost setup is changed when ntfy is already
running, the event handler `events/smarthost-changed/10reload_services`
restarts the main module service.

See also the `systemd/user/ntfy.service` file.

This setting discovery is just an example to understand how the module is
expected to work: it can be rewritten or discarded completely.

## Debug

some CLI are needed to debug

- The module runs under an agent that initiate a lot of environment variables (in /home/ntfy1/.config/state), it could be nice to verify them
on the root terminal

    `runagent -m ntfy1 env`

- you can become runagent for testing scripts and initiate all environment variables
  
    `runagent -m ntfy1`

 the path become : 
```
    echo $PATH
    /home/ntfy1/.config/bin:/usr/local/agent/pyenv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/usr/
```

- if you want to debug a container or see environment inside
 `runagent -m ntfy1`
 ```
podman ps
CONTAINER ID  IMAGE                                      COMMAND               CREATED        STATUS        PORTS                    NAMES
d292c6ff28e9  localhost/podman-pause:4.6.1-1702418000                          9 minutes ago  Up 9 minutes  127.0.0.1:20015->80/tcp  80b8de25945f-infra
d8df02bf6f4a  docker.io/library/mariadb:10.11.5          --character-set-s...  9 minutes ago  Up 9 minutes  127.0.0.1:20015->80/tcp  mariadb-app
9e58e5bd676f  docker.io/library/nginx:stable-alpine3.17  nginx -g daemon o...  9 minutes ago  Up 9 minutes  127.0.0.1:20015->80/tcp  ntfy-app
```

you can see what environment variable is inside the container
```
podman exec  ntfy-app env
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
TERM=xterm
PKG_RELEASE=1
MARIADB_DB_HOST=127.0.0.1
MARIADB_DB_NAME=ntfy
MARIADB_IMAGE=docker.io/mariadb:10.11.5
MARIADB_DB_TYPE=mysql
container=podman
NGINX_VERSION=1.24.0
NJS_VERSION=0.7.12
MARIADB_DB_USER=ntfy
MARIADB_DB_PASSWORD=ntfy
MARIADB_DB_PORT=3306
HOME=/root
```

you can run a shell inside the container

```
podman exec -ti   ntfy-app sh
/ # 
```
## Testing

Test the module using the `test-module.sh` script:


    ./test-module.sh <NODE_ADDR> ghcr.io/geniusdynamics/ntfy:latest

The tests are made using [Robot Framework](https://robotframework.org/)

## UI translation

Translated with [Weblate](https://hosted.weblate.org/projects/ns8/).

To setup the translation process:

- add [GitHub Weblate app](https://docs.weblate.org/en/latest/admin/continuous.html#github-setup) to your repository
- add your repository to [hosted.weblate.org]((https://hosted.weblate.org) or ask a nethserver developer to add it to ns8 Weblate project
