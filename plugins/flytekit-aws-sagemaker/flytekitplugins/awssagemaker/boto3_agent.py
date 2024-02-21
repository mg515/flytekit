from typing import Optional

from flyteidl.core.execution_pb2 import TaskExecution

from flytekit import FlyteContextManager
from flytekit.core.type_engine import TypeEngine
from flytekit.extend.backend.base_agent import (
    AgentRegistry,
    Resource,
    SyncAgentBase,
)
from flytekit.extend.backend.utils import get_agent_secret
from flytekit.models.literals import LiteralMap
from flytekit.models.task import TaskTemplate

from .boto3_mixin import Boto3AgentMixin


def convert_floats_with_no_fraction_to_ints(data):
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, float) and value.is_integer():
                data[key] = int(value)
            elif isinstance(value, dict) or isinstance(value, list):
                convert_floats_with_no_fraction_to_ints(value)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, float) and item.is_integer():
                data[i] = int(item)
            elif isinstance(item, dict) or isinstance(item, list):
                convert_floats_with_no_fraction_to_ints(item)


class BotoAgent(SyncAgentBase):
    """A general purpose boto3 agent that can be used to call any boto3 method."""

    name = "Boto Agent"

    def __init__(self):
        super().__init__(task_type_name="boto")

    async def do(self, task_template: TaskTemplate, inputs: Optional[LiteralMap] = None, **kwargs) -> Resource:
        custom = task_template.custom
        service = custom["service"]
        raw_config = custom["config"]
        convert_floats_with_no_fraction_to_ints(raw_config)
        config = raw_config
        region = custom["region"]
        method = custom["method"]

        boto3_object = Boto3AgentMixin(service=service, region=region)

        result = await boto3_object._call(
            method=method,
            config=config,
            container=task_template.container,
            inputs=inputs,
            aws_access_key_id=get_agent_secret(secret_key="aws-access-key"),
            aws_secret_access_key=get_agent_secret(secret_key="aws-secret-access-key"),
            aws_session_token=get_agent_secret(secret_key="aws-session-token"),
        )

        outputs = None
        if result:
            ctx = FlyteContextManager.current_context()
            outputs = LiteralMap(
                {
                    "result": TypeEngine.to_literal(
                        ctx,
                        result,
                        dict,
                        TypeEngine.to_literal_type(dict),
                    )
                }
            )

        return Resource(phase=TaskExecution.SUCCEEDED, outputs=outputs)


AgentRegistry.register(BotoAgent())
