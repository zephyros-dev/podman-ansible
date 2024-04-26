import os

import anyio
import dagger


async def ci():
    async with dagger.Connection(dagger.Config()) as client:
        user_dir = "/root"
        workspace = client.host().directory(".")

        ci = (
            client.container()
            .build(context=workspace, dockerfile="ci/Dockerfile")
            .with_exec(["rm", "-f", "/etc/apt/apt.conf.d/docker-clean"])
            .with_new_file(
                path="/etc/apt/apt.conf.d/keep-cache",
                contents='Binary::apt::APT::Keep-Downloaded-Packages "true";',
                permissions=644,
            )
            .with_mounted_cache(
                path="/var/cache/apt",
                cache=client.cache_volume(key="ci-cache-cache-apt"),
                sharing=dagger.CacheSharingMode.LOCKED,
            )
            .with_mounted_cache(
                path="/var/lib/apt",
                cache=client.cache_volume(key="ci-cache-lib-apt"),
                sharing=dagger.CacheSharingMode.LOCKED,
            )
            .with_exec(["apt", "update"])
            .with_exec(
                [
                    "apt",
                    "install",
                    "-y",
                    "--no-install-recommends",
                    "curl",
                    "openssh-client",
                    "openssl",
                    "rsync",
                    "sshpass",
                    "tar",
                    "whois",
                ]
            )
        )

        ci = (
            ci.with_unix_socket(
                "/ssh-agent.sock",
                client.host().unix_socket(os.getenv("SSH_AUTH_SOCK")),
            )
            .with_mounted_directory(f"{user_dir}/workspace", workspace)
            .with_workdir(f"{user_dir}/workspace")
            .with_mounted_cache(
                "/root/.cache/pip",
                cache=client.cache_volume(key="ci-cache-pip"),
                sharing=dagger.CacheSharingMode.LOCKED,
            )
            .with_exec(
                [
                    "pip",
                    "install",
                    "--no-warn-script-location",
                    "-r",
                    "ci/requirements/ci/requirements.txt",
                ]
            )
        )

        container_path = await ci.env_variable("PATH")

        ci = (
            ci.with_env_variable(
                "PATH", f"{user_dir}/.local/share/aquaproj-aqua/bin:{container_path}"
            )
            .with_mounted_cache(
                f"{user_dir}/.local/share/aquaproj-aqua/pkgs",
                client.cache_volume("aqua-pkgs"),
            )
            .with_mounted_cache(
                f"{user_dir}/.local/share/aquaproj-aqua/registries",
                client.cache_volume("aqua-registries"),
            )
            .with_mounted_cache(
                f"{user_dir}/.local/share/aquaproj-aqua/bin",
                client.cache_volume("aqua-bin"),
            )
            .with_exec([".devcontainer/main.py", "--stage=dependency"])
        )

        ci = (
            ci.with_mounted_cache(
                f"{user_dir}/.ansible", client.cache_volume("ansible_cache")
            )
            .with_exec(["ansible-galaxy", "install", "-r", "requirements.yaml"])
            .with_env_variable("ANSIBLE_HOST_KEY_CHECKING", "False")
        )

        ansible_config = {
            "ANSIBLE_CALLBACKS_ENABLED": "timer",
            "ANSIBLE_DISPLAY_SKIPPED_HOSTS": "False",
            "ANSIBLE_STDOUT_CALLBACK": "dense",
        }

        if os.getenv("DEBUG_MODE") == "true":
            pass
        else:
            for key, value in ansible_config.items():
                ci = ci.with_env_variable(key, value)

        age_key_path = f"{os.environ['HOME']}/.config/sops/age/keys.txt"
        if os.path.exists(age_key_path):
            ci = ci.with_mounted_directory(
                f"{user_dir}/.config/sops/age",
                client.host().directory(os.path.dirname(age_key_path)),
            )
        else:
            secret_sops_env = client.set_secret(
                "secret_sops_env", os.getenv("SOPS_AGE_KEY")
            )
            ci = ci.with_secret_variable("SOPS_AGE_KEY", secret_sops_env)

        ci = ci.with_exec(["ansible-playbook", "main.yaml"])

        await ci.stdout()


if __name__ == "__main__":
    anyio.run(ci)
