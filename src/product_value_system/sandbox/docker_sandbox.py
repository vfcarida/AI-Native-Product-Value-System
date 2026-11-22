"""
Compatibility facade exporting ``DockerSandboxProvider`` at
``product_value_system.sandbox.docker_sandbox`` (EVO-029).
"""

from product_value_system.runtime.docker_sandbox import DockerSandboxProvider

__all__ = ["DockerSandboxProvider"]
