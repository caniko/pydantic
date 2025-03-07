from __future__ import annotations as _annotations

import typing
from typing import Any

import typing_extensions
from pydantic_core import CoreSchema, core_schema

if typing.TYPE_CHECKING:
    from ..json_schema import JsonSchemaValue
    from ._json_schema_shared import (
        CoreSchemaOrField as CoreSchemaOrField,
    )
    from ._json_schema_shared import (
        GetJsonSchemaFunction,
        GetJsonSchemaHandler,
    )


class CoreMetadata(typing_extensions.TypedDict, total=False):
    # `pydantic_cs_update_function Retrieves the function that will be used to update the CoreSchema.
    # This is generally obtained from a `__pydantic_update_schema__` function
    pydantic_cs_update_function: UpdateCoreSchemaCallable | None

    pydantic_js_functions: list[GetJsonSchemaFunction]

    # If `pydantic_js_prefer_positional_arguments` is True, the JSON schema generator will
    # prefer positional over keyword arguments for an 'arguments' schema.
    pydantic_js_prefer_positional_arguments: bool | None


class UpdateCoreSchemaCallable(typing_extensions.Protocol):
    def __call__(self, schema: CoreSchema, **kwargs: Any) -> None:
        ...


class CoreMetadataHandler:
    """
    Because the metadata field in pydantic_core is of type `Any`, we can't assume much about its contents.

    This class is used to interact with the metadata field on a CoreSchema object in a consistent
    way throughout pydantic.
    """

    __slots__ = ('_schema',)

    def __init__(self, schema: CoreSchema | core_schema.TypedDictField | core_schema.DataclassField):
        self._schema = schema

        metadata = schema.get('metadata')
        if metadata is None:
            schema['metadata'] = CoreMetadata()
        elif not isinstance(metadata, dict):
            raise TypeError(f'CoreSchema metadata should be a dict; got {metadata!r}.')

    @property
    def metadata(self) -> CoreMetadata:
        """
        Retrieves the metadata dict off the schema, initializing it to a dict if it is None
        and raises an error if it is not a dict.
        """
        metadata = self._schema.get('metadata')
        if metadata is None:
            self._schema['metadata'] = metadata = CoreMetadata()
        if not isinstance(metadata, dict):
            raise TypeError(f'CoreSchema metadata should be a dict; got {metadata!r}.')
        return metadata  # type: ignore[return-value]

    def get_json_schema(
        self,
        core_schema: CoreSchema | core_schema.TypedDictField | core_schema.DataclassField,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        js_function = self.metadata.get('pydantic_js_function')
        if js_function is None:
            return handler(core_schema)
        return js_function(core_schema, handler)


def build_metadata_dict(
    *,  # force keyword arguments to make it easier to modify this signature in a backwards-compatible way
    cs_update_function: UpdateCoreSchemaCallable | None = None,
    js_functions: list[GetJsonSchemaFunction] | None = None,
    js_prefer_positional_arguments: bool | None = None,
    initial_metadata: Any | None = None,
) -> Any:
    """
    Builds a dict to use as the metadata field of a CoreSchema object in a manner that is consistent
    with the CoreMetadataHandler class.
    """
    if initial_metadata is not None and not isinstance(initial_metadata, dict):
        raise TypeError(f'CoreSchema metadata should be a dict; got {initial_metadata!r}.')

    metadata = CoreMetadata(
        pydantic_cs_update_function=cs_update_function,
        pydantic_js_functions=js_functions or [],
        pydantic_js_prefer_positional_arguments=js_prefer_positional_arguments,
    )
    metadata = {k: v for k, v in metadata.items() if v is not None}  # type: ignore[assignment]

    if initial_metadata is not None:
        metadata = {**initial_metadata, **metadata}  # type: ignore[misc]

    return metadata
