from __future__ import annotations

import base64
import datetime
import enum
import gzip
import os
import re
import tempfile
import typing
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dataclasses_json import dataclass_json
from docker_image import reference

from flytekit.configuration import internal as _internal
from flytekit.configuration.file import ConfigEntry, ConfigFile, get_config_file, set_if_exists

PROJECT_PLACEHOLDER = "{{ registration.project }}"
DOMAIN_PLACEHOLDER = "{{ registration.domain }}"
VERSION_PLACEHOLDER = "{{ registration.version }}"
DEFAULT_RUNTIME_PYTHON_INTERPRETER = "/opt/venv/bin/python3"
DEFAULT_FLYTEKIT_ENTRYPOINT_FILELOC = "bin/entrypoint.py"
DEFAULT_IMAGE_NAME = "default"
DEFAULT_IN_CONTAINER_SRC_PATH = "/root"
_IMAGE_FQN_TAG_REGEX = re.compile(r"([^:]+)(?=:.+)?")


@dataclass_json
@dataclass(init=True, repr=True, eq=True, frozen=True)
class Image(object):
    """
    Image is a structured wrapper for task container images used in object serialization.

    Attributes:
        name (str): A user-provided name to identify this image.
        fqn (str): Fully qualified image name. This consists of
            #. a registry location
            #. a username
            #. a repository name
            For example: `hostname/username/reponame`
        tag (str): Optional tag used to specify which version of an image to pull
    """

    name: str
    fqn: str
    tag: str

    @property
    def full(self) -> str:
        """ "
        Return the full image name with tag.
        """
        return f"{self.fqn}:{self.tag}"

    @staticmethod
    def look_up_image_info(name: str, tag: str, optional_tag: bool = False) -> Image:
        """
        Looks up the image tag from environment variable (should be set from the Dockerfile).
            FLYTE_INTERNAL_IMAGE should be the environment variable.

        This function is used when registering tasks/workflows with Admin.
        When using the canonical Python-based development cycle, the version that is used to register workflows
        and tasks with Admin should be the version of the image itself, which should ideally be something unique
        like the sha of the latest commit.

        :param optional_tag:
        :param name:
        :param Text tag: e.g. somedocker.com/myimage:someversion123
        :rtype: Text
        """
        ref = reference.Reference.parse(tag)
        if not optional_tag and ref["tag"] is None:
            raise AssertionError(f"Incorrectly formatted image {tag}, missing tag value")
        else:
            return Image(name=name, fqn=ref["name"], tag=ref["tag"])


@dataclass_json
@dataclass(init=True, repr=True, eq=True, frozen=True)
class ImageConfig(object):
    """
    ImageConfig holds available images which can be used at registration time. A default image can be specified
    along with optional additional images. Each image in the config must have a unique name.

    Attributes:
        default_image (str): The default image to be used as a container for task serialization.
        images (List[Image]): Optional, additional images which can be used in task container definitions.
    """

    default_image: Optional[Image] = None
    images: Optional[List[Image]] = None

    def find_image(self, name) -> Optional[Image]:
        """
        Return an image, by name, if it exists.
        """
        lookup_images = self.images + [self.default_image] if self.images else [self.default_image]
        for i in lookup_images:
            if i.name == name:
                return i
        return None

    @staticmethod
    def validate_image(ctx: typing.Any, param: str, values: tuple) -> ImageConfig:
        """
        Validates the image to match the standard format. Also validates that only one default image
        is provided. a default image, is one that is specified as
          default=img or just img. All other images should be provided with a name, in the format
          name=img
        """
        default_image = None
        images = []
        for v in values:
            if "=" in v:
                splits = v.split("=", maxsplit=1)
                img = Image.look_up_image_info(name=splits[0], tag=splits[1], optional_tag=False)
            else:
                img = Image.look_up_image_info(DEFAULT_IMAGE_NAME, v, False)

            if default_image and img.name == DEFAULT_IMAGE_NAME:
                raise ValueError(
                    f"Only one default image can be specified. Received multiple {default_image} & {img} for {param}"
                )
            if img.name == DEFAULT_IMAGE_NAME:
                default_image = img
            else:
                images.append(img)

        return ImageConfig(default_image, images)

    @classmethod
    def auto(cls, config_file: typing.Union[str, ConfigFile] = None, img_name: Optional[str] = None) -> ImageConfig:
        """
        Reads from config file or from img_name
        :param config_file:
        :param img_name:
        :return:
        """
        if config_file is None and img_name is None:
            raise ValueError("Either an image or a config with a default image should be provided")

        default_img = Image.look_up_image_info("default", img_name) if img_name is not None and img_name != "" else None
        all_images = [default_img]

        other_images = []
        if config_file:
            config_file = get_config_file(config_file)
            other_images = [
                Image.look_up_image_info(k, tag=v, optional_tag=True)
                for k, v in _internal.Images.get_specified_images(config_file).items()
            ]
        all_images.extend(other_images)
        return ImageConfig(default_image=default_img, images=all_images)


class AuthType(enum.Enum):
    STANDARD = "standard"
    BASIC = "basic"
    CLIENT_CREDENTIALS = "client_credentials"
    EXTERNAL_PROCESS = "external_process"


@dataclass(init=True, repr=True, eq=True, frozen=True)
class PlatformConfig(object):
    endpoint: str = "localhost:30081"
    insecure: bool = False
    command: typing.Optional[str] = None
    """
    This command is executed to return a token using an external process.
    """
    client_id: typing.Optional[str] = None
    """
    This is the public identifier for the app which handles authorization for a Flyte deployment.
    More details here: https://www.oauth.com/oauth2-servers/client-registration/client-id-secret/.
    """
    client_credentials_secret: typing.Optional[str] = None
    """
    Used for service auth, which is automatically called during pyflyte. This will allow the Flyte engine to read the
    password directly from the environment variable. Note that this is less secure! Please only use this if mounting the
    secret as a file is impossible.
    """
    scopes: List[str] = field(default_factory=list)
    auth_mode: AuthType = AuthType.STANDARD

    @classmethod
    def auto(cls, config_file: typing.Union[str, ConfigFile] = None) -> PlatformConfig:
        """
        Reads from Config file, and overrides from Environment variables. Refer to ConfigEntry for details
        :param config_file:
        :return:
        """
        config_file = get_config_file(config_file)
        kwargs = {}
        kwargs = set_if_exists(kwargs, "insecure", _internal.Platform.INSECURE.read(config_file))
        kwargs = set_if_exists(kwargs, "command", _internal.Credentials.COMMAND.read(config_file))
        kwargs = set_if_exists(kwargs, "client_id", _internal.Credentials.CLIENT_ID.read(config_file))
        kwargs = set_if_exists(
            kwargs, "client_credentials_secret", _internal.Credentials.CLIENT_CREDENTIALS_SECRET.read(config_file)
        )
        kwargs = set_if_exists(kwargs, "scopes", _internal.Credentials.SCOPES.read(config_file))
        kwargs = set_if_exists(kwargs, "auth_mode", _internal.Credentials.AUTH_MODE.read(config_file))
        kwargs = set_if_exists(kwargs, "endpoint", _internal.Platform.URL.read(config_file))
        return PlatformConfig(**kwargs)

    @classmethod
    def for_endpoint(cls, endpoint: str, insecure: bool = False) -> PlatformConfig:
        return PlatformConfig(endpoint=endpoint, insecure=insecure)


@dataclass(init=True, repr=True, eq=True, frozen=True)
class StatsConfig(object):
    host: str = "localhost"
    port: int = 8125
    disabled: bool = False
    disabled_tags: bool = False

    @classmethod
    def auto(cls, config_file: typing.Union[str, ConfigFile] = None) -> StatsConfig:
        """
        Reads from environment variable, followed by ConfigFile provided
        :param config_file:
        :return:
        """
        config_file = get_config_file(config_file)
        kwargs = {}
        kwargs = set_if_exists(kwargs, "host", _internal.StatsD.HOST.read(config_file))
        kwargs = set_if_exists(kwargs, "port", _internal.StatsD.PORT.read(config_file))
        kwargs = set_if_exists(kwargs, "disabled", _internal.StatsD.DISABLED.read(config_file))
        kwargs = set_if_exists(kwargs, "disabled_tags", _internal.StatsD.DISABLE_TAGS.read(config_file))
        return StatsConfig(**kwargs)


@dataclass(init=True, repr=True, eq=True, frozen=True)
class SecretsConfig(object):
    env_prefix: str = "_FSEC_"
    default_dir: str = os.path.join(os.sep, "etc", "secrets")
    file_prefix: str = ""

    @classmethod
    def auto(cls, config_file: typing.Union[str, ConfigFile] = None) -> SecretsConfig:
        """
        Reads from environment variable or from config file
        :param config_file:
        :return:
        """
        config_file = get_config_file(config_file)
        kwargs = {}
        kwargs = set_if_exists(kwargs, "env_prefix", _internal.Secrets.ENV_PREFIX.read(config_file))
        kwargs = set_if_exists(kwargs, "default_prefix", _internal.Secrets.DEFAULT_DIR.read(config_file))
        kwargs = set_if_exists(kwargs, "file_prefix", _internal.Secrets.FILE_PREFIX.read(config_file))
        return SecretsConfig(**kwargs)


@dataclass
class S3Config(object):
    """
    S3 specific configuration
    """

    enable_debug: bool = False
    endpoint: typing.Optional[str] = None
    retries: int = 3
    backoff: datetime.timedelta = datetime.timedelta(seconds=5)
    access_key_id: typing.Optional[str] = None
    secret_access_key: typing.Optional[str] = None

    @classmethod
    def auto(cls, config_file: typing.Union[str, ConfigFile] = None) -> S3Config:
        """
        Automatically configure
        :param config_file:
        :return: Configr
        """
        config_file = get_config_file(config_file)
        kwargs = {}
        kwargs = set_if_exists(kwargs, "enable_debug", _internal.AWS.ENABLE_DEBUG.read(config_file))
        kwargs = set_if_exists(kwargs, "endpoint", _internal.AWS.S3_ENDPOINT.read(config_file))
        kwargs = set_if_exists(kwargs, "retries", _internal.AWS.RETRIES.read(config_file))
        kwargs = set_if_exists(kwargs, "backoff", _internal.AWS.BACKOFF_SECONDS.read(config_file))
        kwargs = set_if_exists(kwargs, "access_key_id", _internal.AWS.S3_ACCESS_KEY_ID.read(config_file))
        kwargs = set_if_exists(kwargs, "secret_access_key", _internal.AWS.S3_SECRET_ACCESS_KEY.read(config_file))
        return S3Config(**kwargs)


@dataclass
class GCSConfig(object):
    """
    Any GCS specific configuration.
    """

    gsutil_parallelism: bool = False

    @classmethod
    def auto(self, config_file: typing.Union[str, ConfigFile] = None) -> GCSConfig:
        config_file = get_config_file(config_file)
        kwargs = {}
        kwargs = set_if_exists(kwargs, "gsutil_parallelism", _internal.GCP.GSUTIL_PARALLELISM.read(config_file))
        return GCSConfig(**kwargs)


@dataclass(init=True, repr=True, eq=True, frozen=True)
class DataConfig(object):
    """
    Any data storage specific configuration. Please do not use this to store secrets, in S3 case, as it is used in
    Flyte sandbox environment we store the access key id and secret.
    All DataPersistence plugins are passed all DataConfig and the plugin should correctly use the right config
    """

    s3: S3Config = S3Config()
    gcs: GCSConfig = GCSConfig()

    @classmethod
    def auto(cls, config_file: typing.Union[str, ConfigFile] = None) -> DataConfig:
        config_file = get_config_file(config_file)
        return DataConfig(
            s3=S3Config.auto(config_file),
            gcs=GCSConfig.auto(config_file),
        )


@dataclass(init=True, repr=True, eq=True, frozen=True)
class Config(object):
    """
    This object represents the environment for Flytekit to perform either
       1. Interactive session with Flyte backend
       2. Some parts are required for Serialization, for example Platform Config is not required
       3. Runtime of a task
    Args:
        entrypoint_settings: EntrypointSettings object for use with Spark tasks. If supplied, this will be
          used when serializing Spark tasks, which need to know the path to the flytekit entrypoint.py file,
          inside the container.
    """

    platform: PlatformConfig = PlatformConfig()
    secrets: SecretsConfig = SecretsConfig()
    stats: StatsConfig = StatsConfig()
    data_config: DataConfig = DataConfig()
    local_sandbox_path: str = tempfile.mkdtemp(prefix="flyte")

    def with_params(
        self,
        platform: PlatformConfig = None,
        secrets: SecretsConfig = None,
        stats: StatsConfig = None,
        data_config: DataConfig = None,
        local_sandbox_path: str = None,
    ) -> Config:
        return Config(
            platform=platform or self.platform,
            secrets=secrets or self.secrets,
            stats=stats or self.stats,
            data_config=data_config or self.data_config,
            local_sandbox_path=local_sandbox_path or self.local_sandbox_path,
        )

    @classmethod
    def auto(cls, config_file: typing.Union[str, ConfigFile] = None) -> Config:
        """
        Automatically constructs the Config Object. The order of precendence is as follows
          1. first try to find any env vars that match the config vars specified in the FLYTE_CONFIG format.
          2. If not found in environment then values ar read from the config file
          3. If not found in the file, then the default values are used.
        :param config_file: file path to read the config from, if not specified default locations are searched
        :return: Config
        """
        config_file = get_config_file(config_file)
        kwargs = {}
        set_if_exists(kwargs, "local_sandbox_path", _internal.LocalSDK.LOCAL_SANDBOX.read(cfg=config_file))
        return Config(
            platform=PlatformConfig.auto(config_file),
            secrets=SecretsConfig.auto(config_file),
            stats=StatsConfig.auto(config_file),
            data_config=DataConfig.auto(config_file),
            **kwargs,
        )

    @classmethod
    def for_sandbox(cls) -> Config:
        """
        Constructs a new Config object specifically to connect to :std:ref:`deploy-sandbox-local`.
        If you are using a hosted Sandbox like environment, then you may need to use port-forward or ingress urls
        :return: Config
        """
        return Config(
            platform=PlatformConfig(insecure=True),
            data_config=DataConfig(
                s3=S3Config(endpoint="localhost:30084", access_key_id="minio", secret_access_key="miniostorage")
            ),
        )

    @classmethod
    def for_endpoint(
        cls,
        endpoint: str,
        insecure: bool = False,
        data_config: typing.Optional[DataConfig] = None,
        config_file: typing.Union[str, ConfigFile] = None,
    ) -> Config:
        """
        Creates an automatic config for the given endpoint and uses the config_file or environment variable for default.
        Refer to `Config.auto()` to understand the default bootstrap behavior.

        data_config can be used to configure how data is downloaded or uploaded to a specific Blob storage like S3 / GCS etc.
        But, for permissions to a specific backend just use Cloud providers reqcommendation. If using fsspec, then
        refer to fsspec documentation
        :param endpoint: -> Endpoint where Flyte admin is available
        :param insecure: -> if the conection should be inseucre
        :param data_config: -> Data config, if using specialized connection params like minio etc
        :param config_file: -> Optional config file in the flytekit config format or flytectl formatl.
        :return: Config
        """
        c = cls.auto(config_file)
        return c.with_params(platform=PlatformConfig.for_endpoint(endpoint, insecure), data_config=data_config)


@dataclass_json
@dataclass
class EntrypointSettings(object):
    """
    This object carries information about the command, path and version of the entrypoint program that will be invoked
    to execute tasks at runtime.
    """

    path: Optional[str] = None
    command: Optional[str] = None
    version: int = 0


@dataclass_json
@dataclass
class FastSerializationSettings(object):
    """
    This object hold information about settings necessary to serialize an object so that it can be fast-registered.
    """

    enabled: bool = False
    # This is the location that the code should be copied into.
    destination_dir: Optional[str] = None

    # This is the zip file where the new code was uploaded to.
    distribution_location: Optional[str] = None


@dataclass_json
@dataclass(frozen=True)
class SerializationSettings(object):
    """
    These settings are provided while serializing a workflow and task, before registration. This is required to get
    runtime information at serialization time, as well as some defaults.

    Attributes:
        project (str): The project (if any) with which to register entities under.
        domain (str): The domain (if any) with which to register entities under.
        version (str): The version (if any) with which to register entities under.
        image_config (ImageConfig): The image config used to define task container images.
        env (Optional[Dict[str, str]]): Environment variables injected into task container definitions.
        flytekit_virtualenv_root (Optional[str]):  During out of container serialize the absolute path of the flytekit
            virtualenv at serialization time won't match the in-container value at execution time. This optional value
            is used to provide the in-container virtualenv path
        python_interpreter (Optional[str]): The python executable to use. This is used for spark tasks in out of
            container execution.
        entrypoint_settings (Optional[EntrypointSettings]): Information about the command, path and version of the
            entrypoint program.
        fast_serialization_settings (Optional[FastSerializationSettings]): If the code is being serialized so that it
            can be fast registered (and thus omit building a Docker image) this object contains additional parameters
            for serialization.
    """

    image_config: ImageConfig
    project: typing.Optional[str] = None
    domain: typing.Optional[str] = None
    version: typing.Optional[str] = None
    env: Optional[Dict[str, str]] = None
    flytekit_virtualenv_root: Optional[str] = None
    python_interpreter: Optional[str] = None
    entrypoint_settings: Optional[EntrypointSettings] = None
    fast_serialization_settings: Optional[FastSerializationSettings] = None

    @dataclass
    class Builder(object):
        project: str
        domain: str
        version: str
        image_config: ImageConfig
        env: Optional[Dict[str, str]] = None
        flytekit_virtualenv_root: Optional[str] = None
        python_interpreter: Optional[str] = None
        entrypoint_settings: Optional[EntrypointSettings] = None
        fast_serialization_settings: Optional[FastSerializationSettings] = None

        def with_fast_serialization_settings(self, fss: fast_serialization_settings) -> SerializationSettings.Builder:
            self.fast_serialization_settings = fss
            return self

        def build(self) -> SerializationSettings:
            return SerializationSettings(
                project=self.project,
                domain=self.domain,
                version=self.version,
                image_config=self.image_config,
                env=self.env,
                flytekit_virtualenv_root=self.flytekit_virtualenv_root,
                python_interpreter=self.python_interpreter,
                entrypoint_settings=self.entrypoint_settings,
                fast_serialization_settings=self.fast_serialization_settings,
            )

    @classmethod
    def from_transport(cls, s: str) -> SerializationSettings:
        compressed_val = base64.b64decode(s.encode("utf-8"))
        json_str = gzip.decompress(compressed_val).decode("utf-8")
        return cls.from_json(json_str)

    @classmethod
    def for_image(
        cls,
        image: str,
        version: str,
        project: str = "",
        domain: str = "",
        python_interpreter_path: str = DEFAULT_RUNTIME_PYTHON_INTERPRETER,
    ) -> SerializationSettings:
        img = ImageConfig(default_image=Image.look_up_image_info(DEFAULT_IMAGE_NAME, tag=image))
        entrypoint_settings = cls.default_entrypoint_settings(python_interpreter_path)
        return SerializationSettings(
            image_config=img,
            project=project,
            domain=domain,
            version=version,
            entrypoint_settings=entrypoint_settings,
            python_interpreter=python_interpreter_path,
            flytekit_virtualenv_root=cls.venv_root_from_interpreter(python_interpreter_path),
        )

    def new_builder(self) -> Builder:
        """
        Creates a ``SerializationSettings.Builder`` that copies the existing serialization settings parameters and
        allows for customization.
        """
        return SerializationSettings.Builder(
            project=self.project,
            domain=self.domain,
            version=self.version,
            image_config=self.image_config,
            env=self.env,
            flytekit_virtualenv_root=self.flytekit_virtualenv_root,
            python_interpreter=self.python_interpreter,
            entrypoint_settings=self.entrypoint_settings,
            fast_serialization_settings=self.fast_serialization_settings,
        )

    def should_fast_serialize(self) -> bool:
        """
        Whether or not the serialization settings specify that entities should be serialized for fast registration.
        """
        return self.fast_serialization_settings is not None and self.fast_serialization_settings.enabled

    def prepare_for_transport(self) -> str:
        json_str = self.to_json()
        compressed_value = gzip.compress(json_str.encode("utf-8"))
        return base64.b64encode(compressed_value).decode("utf-8")

    @staticmethod
    def venv_root_from_interpreter(interpreter_path: str) -> str:
        """
        Computes the path of the virtual environment root, based on the passed in python interpreter path
        for example /opt/venv/bin/python3 -> /opt/venv
        """
        return os.path.dirname(os.path.dirname(interpreter_path))

    @staticmethod
    def default_entrypoint_settings(interpreter_path: str) -> EntrypointSettings:
        """
        Assumes the entrypoint is installed in a virtual-environment where the interpreter is
        """
        return EntrypointSettings(
            path=os.path.join(
                SerializationSettings.venv_root_from_interpreter(interpreter_path), DEFAULT_FLYTEKIT_ENTRYPOINT_FILELOC
            )
        )
