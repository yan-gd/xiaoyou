# -*- coding:utf-8 -*-
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM_COMMIT = "22d67b3a596f8c96cc2f8b2e5ed58a47c8bb53bb"


def read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as handle:
        return handle.read()


class LocalUpstreamRebuildTest(unittest.TestCase):
    def test_dockerfile_builds_from_vendored_source(self):
        source = read("Dockerfile")
        self.assertIn("FROM python:3.10-slim-bullseye", source)
        self.assertIn("COPY vendor/chatgpt-on-wechat-1.7.3/ ${BUILD_PREFIX}/", source)
        self.assertNotIn("FROM zhayujie/chatgpt-on-wechat", source)
        self.assertIn(UPSTREAM_COMMIT, source)

    def test_vendored_release_contains_runtime_license_and_metadata(self):
        for path in (
            ("vendor", "chatgpt-on-wechat-1.7.3", "app.py"),
            ("vendor", "chatgpt-on-wechat-1.7.3", "requirements.txt"),
            ("vendor", "chatgpt-on-wechat-1.7.3", "docker", "entrypoint.sh"),
            ("vendor", "chatgpt-on-wechat-1.7.3", "LICENSE"),
        ):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, *path)), "/".join(path))

        metadata = read("vendor", "README.md")
        self.assertIn("chatgpt-on-wechat-1.7.3", metadata)
        self.assertIn("Release tag: `1.7.3`", metadata)
        self.assertIn(UPSTREAM_COMMIT, metadata)

    def test_compose_declares_the_local_build(self):
        source = read("docker-compose.yml")
        service = source[source.index("chatgpt-on-wechat:"):]
        self.assertIn("build:", service)
        self.assertIn("context: .", service)
        self.assertIn("dockerfile: Dockerfile", service)
        self.assertIn("image: cow-legacy-local:vision-no-think", service)

    def test_build_context_does_not_include_runtime_secrets(self):
        rules = read(".dockerignore")
        self.assertTrue(rules.lstrip().startswith("**"))
        self.assertIn("!patches/**", rules)
        self.assertIn("!vendor/chatgpt-on-wechat-1.7.3/**", rules)
        self.assertNotIn("!.env", rules)
        self.assertNotIn("!data/", rules)


if __name__ == "__main__":
    unittest.main()
