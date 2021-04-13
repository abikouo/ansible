"""CloudStack plugin for integration tests."""
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json
import os

from . import (
    CloudProvider,
    CloudEnvironment,
    CloudEnvironmentConfig,
)

from ..util import (
    ApplicationError,
    display,
    ConfigParser,
)

from ..http import (
    urlparse,
)

from ..docker_util import (
    docker_exec,
)

from ..containers import (
    run_support_container,
    wait_for_file,
)


class CsCloudProvider(CloudProvider):
    """CloudStack cloud provider plugin. Sets up cloud resources before delegation."""
    DOCKER_SIMULATOR_NAME = 'cloudstack-sim'

    def __init__(self, args):
        """
        :type args: TestConfig
        """
        super(CsCloudProvider, self).__init__(args)

        self.image = os.environ.get('ANSIBLE_CLOUDSTACK_CONTAINER', 'quay.io/ansible/cloudstack-test-container:1.4.0')
        self.host = ''
        self.port = 0

        self.uses_docker = True
        self.uses_config = True

    def setup(self):
        """Setup the cloud resource before delegation and register a cleanup callback."""
        super(CsCloudProvider, self).setup()

        if self._use_static_config():
            self._setup_static()
        else:
            self._setup_dynamic()

    def _setup_static(self):
        """Configure CloudStack tests for use with static configuration."""
        parser = ConfigParser()
        parser.read(self.config_static_path)

        endpoint = parser.get('cloudstack', 'endpoint')

        parts = urlparse(endpoint)

        self.host = parts.hostname

        if not self.host:
            raise ApplicationError('Could not determine host from endpoint: %s' % endpoint)

        if parts.port:
            self.port = parts.port
        elif parts.scheme == 'http':
            self.port = 80
        elif parts.scheme == 'https':
            self.port = 443
        else:
            raise ApplicationError('Could not determine port from endpoint: %s' % endpoint)

        display.info('Read cs host "%s" and port %d from config: %s' % (self.host, self.port, self.config_static_path), verbosity=1)

    def _setup_dynamic(self):
        """Create a CloudStack simulator using docker."""
        config = self._read_config_template()

        self.port = 8888

        ports = [
            self.port,
        ]

        descriptor = run_support_container(
            self.args,
            self.platform,
            self.image,
            self.DOCKER_SIMULATOR_NAME,
            ports,
            allow_existing=True,
            cleanup=True,
        )

        descriptor.register(self.args)

        # apply work-around for OverlayFS issue
        # https://github.com/docker/for-linux/issues/72#issuecomment-319904698
        docker_exec(self.args, self.DOCKER_SIMULATOR_NAME, ['find', '/var/lib/mysql', '-type', 'f', '-exec', 'touch', '{}', ';'])

        if self.args.explain:
            values = dict(
                HOST=self.host,
                PORT=str(self.port),
            )
        else:
            credentials = self._get_credentials(self.DOCKER_SIMULATOR_NAME)

            values = dict(
                HOST=self.DOCKER_SIMULATOR_NAME,
                PORT=str(self.port),
                KEY=credentials['apikey'],
                SECRET=credentials['secretkey'],
            )

            display.sensitive.add(values['SECRET'])

        config = self._populate_config_template(config, values)

        self._write_config(config)

    def _get_credentials(self, container_name):
        """Wait for the CloudStack simulator to return credentials.
        :type container_name: str
        :rtype: dict[str, str]
        """
        def check(value):
            # noinspection PyBroadException
            try:
                json.loads(value)
            except Exception:   # pylint: disable=broad-except
                return False  # sometimes the file exists but is not yet valid JSON

            return True

        stdout = wait_for_file(self.args, container_name, '/var/www/html/admin.json', sleep=10, tries=30, check=check)

        return json.loads(stdout)


class CsCloudEnvironment(CloudEnvironment):
    """CloudStack cloud environment plugin. Updates integration test environment after delegation."""
    def get_environment_config(self):
        """
        :rtype: CloudEnvironmentConfig
        """
        parser = ConfigParser()
        parser.read(self.config_path)

        config = dict(parser.items('default'))

        env_vars = dict(
            CLOUDSTACK_ENDPOINT=config['endpoint'],
            CLOUDSTACK_KEY=config['key'],
            CLOUDSTACK_SECRET=config['secret'],
            CLOUDSTACK_TIMEOUT=config['timeout'],
        )

        display.sensitive.add(env_vars['CLOUDSTACK_SECRET'])

        ansible_vars = dict(
            cs_resource_prefix=self.resource_prefix,
        )

        return CloudEnvironmentConfig(
            env_vars=env_vars,
            ansible_vars=ansible_vars,
        )
