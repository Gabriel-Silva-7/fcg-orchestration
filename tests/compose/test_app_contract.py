"""Configuration boundary: four real hosts gated by durable infrastructure topology."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]

class ApplicationCompositionTests(unittest.TestCase):
    def test_four_hosts_share_explicit_jwt_and_private_dependencies(self):
        result = subprocess.run(['docker', '--context', 'orbstack', 'compose', '--env-file', str(ROOT/'.env.example'),
                                 '-f', str(ROOT/'docker-compose.yml'), 'config', '--format', 'json'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, 'Compose must render; resolved configuration is kept private')
        services = json.loads(result.stdout)['services']
        for name in ('users', 'catalog', 'payments', 'notifications'):
            self.assertTrue(name in services, 'Compose must include the real application host: '+name)
            service = services[name]
            self.assertIn('build', service)
            self.assertEqual('service_completed_successfully', service['depends_on']['rabbitmq-bootstrap']['condition'])
            self.assertEqual('service_healthy', service['depends_on']['postgres']['condition'])
            env = service['environment']
            self.assertEqual('true', env['Database__MigrateOnStartup'])
            self.assertEqual('/fcg', env['RabbitMq__VirtualHost'])
            self.assertTrue('Database=fcg_'+name in env['ConnectionStrings__Default'], 'Each service needs its own database')
            self.assertEqual('127.0.0.1', service['ports'][0]['host_ip'])
            mounts = service.get('volumes', [])
            private = [mount for mount in mounts if mount['target'].endswith('private.pem')]
            self.assertEqual(1 if name == 'users' else 0, len(private))
            self.assertTrue(all(mount.get('read_only') for mount in mounts))
        for key in ('Jwt__Issuer','Jwt__Audience','Jwt__Algorithm','Jwt__ExpirationMinutes','Jwt__ClockSkewSeconds'):
            self.assertEqual(services['users']['environment'][key], services['catalog']['environment'][key])
        self.assertEqual('false', services['users']['environment']['AdminBootstrap__Enabled'])
        self.assertEqual('', services['users']['environment']['AdminBootstrap__Password'])

if __name__ == '__main__': unittest.main()
