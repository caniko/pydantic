import re
import sys
from collections import deque
from datetime import date, datetime
from enum import Enum
from functools import partial, partialmethod
from itertools import product
from typing import Any, Callable, Deque, Dict, FrozenSet, List, Optional, Tuple, Type, Union
from unittest.mock import MagicMock

import pytest
from typing_extensions import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FieldValidationInfo,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    errors,
    validator,
)
from pydantic.annotated_arguments import AfterValidator, BeforeValidator, PlainValidator, WrapValidator
from pydantic.decorators import field_validator, root_validator


def test_annotated_validator_after() -> None:
    MyInt = Annotated[int, AfterValidator(lambda x: x if x != -1 else 0)]

    class Model(BaseModel):
        x: MyInt

    assert Model(x=0).x == 0
    assert Model(x=-1).x == 0
    assert Model(x=-2).x == -2
    assert Model(x=1).x == 1
    assert Model(x='-1').x == 0


def test_annotated_validator_before() -> None:
    FloatMaybeInf = Annotated[float, BeforeValidator(lambda x: x if x != 'zero' else 0.0)]

    class Model(BaseModel):
        x: FloatMaybeInf

    assert Model(x='zero').x == 0.0
    assert Model(x=1.0).x == 1.0
    assert Model(x='1.0').x == 1.0


def test_annotated_validator_plain() -> None:
    MyInt = Annotated[int, PlainValidator(lambda x: x if x != -1 else 0)]

    class Model(BaseModel):
        x: MyInt

    assert Model(x=0).x == 0
    assert Model(x=-1).x == 0
    assert Model(x=-2).x == -2


def test_annotated_validator_wrap() -> None:
    def sixties_validator(val: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo) -> date:
        if val == 'epoch':
            return date.fromtimestamp(0)
        newval = handler(val)
        if not date.fromisoformat('1960-01-01') <= newval < date.fromisoformat('1970-01-01'):
            raise ValueError(f'{val} is not in the sixties!')
        return newval

    SixtiesDateTime = Annotated[date, WrapValidator(sixties_validator)]

    class Model(BaseModel):
        x: SixtiesDateTime

    assert Model(x='epoch').x == date.fromtimestamp(0)
    assert Model(x='1962-01-13').x == date(year=1962, month=1, day=13)
    assert Model(x=datetime(year=1962, month=1, day=13)).x == date(year=1962, month=1, day=13)

    with pytest.raises(ValidationError) as exc_info:
        Model(x=date(year=1970, month=4, day=17))
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': ('x',),
            'msg': 'Value error, 1970-04-17 is not in the sixties!',
            'input': date(1970, 4, 17),
            'ctx': {'error': '1970-04-17 is not in the sixties!'},
        }
    ]


def test_annotated_validator_nested() -> None:
    MyInt = Annotated[int, AfterValidator(lambda x: x if x != -1 else 0)]

    def non_decreasing_list(data: List[int]) -> List[int]:
        for prev, cur in zip(data, data[1:]):
            assert cur >= prev
        return data

    class Model(BaseModel):
        x: Annotated[List[MyInt], AfterValidator(non_decreasing_list)]

    assert Model(x=[0, -1, 2]).x == [0, 0, 2]

    with pytest.raises(ValidationError) as exc_info:
        Model(x=[0, -1, -2])

    assert exc_info.value.errors() == [
        {
            'type': 'assertion_error',
            'loc': ('x',),
            'msg': 'Assertion failed, assert -2 >= 0',
            'input': [0, -1, -2],
            'ctx': {'error': 'assert -2 >= 0'},
        }
    ]


def test_annotated_validator_runs_before_field_validators() -> None:
    MyInt = Annotated[int, AfterValidator(lambda x: x if x != -1 else 0)]

    class Model(BaseModel):
        x: MyInt

        @field_validator('x')
        def val_x(cls, v: int) -> int:
            assert v != -1
            return v

    assert Model(x=-1).x == 0


@pytest.mark.parametrize(
    'validator, func',
    [
        (PlainValidator, lambda x: x if x != -1 else 0),
        (WrapValidator, lambda x, nxt: x if x != -1 else 0),
        (BeforeValidator, lambda x: x if x != -1 else 0),
        (AfterValidator, lambda x: x if x != -1 else 0),
    ],
)
def test_annotated_validator_typing_cache(validator, func):
    FancyInt = Annotated[int, validator(func)]

    class FancyIntModel(BaseModel):
        x: Optional[FancyInt]

    assert FancyIntModel(x=1234).x == 1234
    assert FancyIntModel(x=-1).x == 0
    assert FancyIntModel(x=0).x == 0


def test_simple():
    class Model(BaseModel):
        a: str

        @field_validator('a')
        @classmethod
        def check_a(cls, v: Any):
            if 'foobar' not in v:
                raise ValueError('"foobar" not found in a')
            return v

    assert Model(a='this is foobar good').a == 'this is foobar good'

    with pytest.raises(ValidationError) as exc_info:
        Model(a='snap')
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': ('a',),
            'msg': 'Value error, "foobar" not found in a',
            'input': 'snap',
            'ctx': {'error': '"foobar" not found in a'},
        }
    ]


def test_int_validation():
    class Model(BaseModel):
        a: int

    with pytest.raises(ValidationError) as exc_info:
        Model(a='snap')
    assert exc_info.value.errors() == [
        {
            'type': 'int_parsing',
            'loc': ('a',),
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'snap',
        }
    ]
    assert Model(a=3).a == 3
    assert Model(a=True).a == 1
    assert Model(a=False).a == 0
    with pytest.raises(ValidationError) as exc_info:
        Model(a=4.5)
    assert exc_info.value.errors() == [
        {
            'type': 'int_from_float',
            'loc': ('a',),
            'msg': 'Input should be a valid integer, got a number with a fractional part',
            'input': 4.5,
        }
    ]

    # Doesn't raise ValidationError for number > (2 ^ 63) - 1 and limits them to (2 ^ 63) - 1
    assert Model(a=(2**63) + 100).a == (2**63) - 1


@pytest.mark.parametrize('value', [2.2250738585072011e308, float('nan'), float('inf')])
def test_int_overflow_validation(value):
    class Model(BaseModel):
        a: int

    with pytest.raises(ValidationError) as exc_info:
        Model(a=value)
    assert exc_info.value.errors() == [
        {'type': 'finite_number', 'loc': ('a',), 'msg': 'Input should be a finite number', 'input': value}
    ]


def test_frozenset_validation():
    class Model(BaseModel):
        a: FrozenSet[int]

    with pytest.raises(ValidationError) as exc_info:
        Model(a='snap')
    assert exc_info.value.errors() == [
        {'type': 'frozen_set_type', 'loc': ('a',), 'msg': 'Input should be a valid frozenset', 'input': 'snap'}
    ]
    assert Model(a={1, 2, 3}).a == frozenset({1, 2, 3})
    assert Model(a=frozenset({1, 2, 3})).a == frozenset({1, 2, 3})
    assert Model(a=[4, 5]).a == frozenset({4, 5})
    assert Model(a=(6,)).a == frozenset({6})
    assert Model(a={'1', '2', '3'}).a == frozenset({1, 2, 3})


def test_deque_validation():
    class Model(BaseModel):
        a: Deque[int]

    with pytest.raises(ValidationError) as exc_info:
        Model(a='snap')
    assert exc_info.value.errors() == [
        {'type': 'list_type', 'loc': ('a',), 'msg': 'Input should be a valid list', 'input': 'snap'}
    ]
    with pytest.raises(ValidationError) as exc_info:
        Model(a=['a'])
    assert exc_info.value.errors() == [
        {
            'type': 'int_parsing',
            'loc': ('a', 0),
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'a',
        }
    ]
    with pytest.raises(ValidationError) as exc_info:
        Model(a=('a',))
    assert exc_info.value.errors() == [
        {
            'type': 'int_parsing',
            'loc': ('a', 0),
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'a',
        }
    ]
    with pytest.raises(ValidationError) as exc_info:
        Model(a={'1'})
    assert exc_info.value.errors() == [
        {'type': 'list_type', 'loc': ('a',), 'msg': 'Input should be a valid list', 'input': {'1'}}
    ]
    assert Model(a=[4, 5]).a == deque([4, 5])
    assert Model(a=(6,)).a == deque([6])


def test_validate_whole():
    class Model(BaseModel):
        a: List[int]

        @field_validator('a', mode='before')
        @classmethod
        def check_a1(cls, v: List[Any]) -> List[Any]:
            v.append('123')
            return v

        @field_validator('a')
        @classmethod
        def check_a2(cls, v: List[int]) -> List[Any]:
            v.append(456)
            return v

    assert Model(a=[1, 2]).a == [1, 2, 123, 456]


def test_validate_pre_error():
    calls = []

    class Model(BaseModel):
        a: List[int]

        @field_validator('a', mode='before')
        @classmethod
        def check_a1(cls, v: Any):
            calls.append(f'check_a1 {v}')
            if 1 in v:
                raise ValueError('a1 broken')
            v[0] += 1
            return v

        @field_validator('a')
        @classmethod
        def check_a2(cls, v: Any):
            calls.append(f'check_a2 {v}')
            if 10 in v:
                raise ValueError('a2 broken')
            return v

    assert Model(a=[3, 8]).a == [4, 8]
    assert calls == ['check_a1 [3, 8]', 'check_a2 [4, 8]']

    calls = []
    with pytest.raises(ValidationError) as exc_info:
        Model(a=[1, 3])
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': ('a',),
            'msg': 'Value error, a1 broken',
            'input': [1, 3],
            'ctx': {'error': 'a1 broken'},
        }
    ]
    assert calls == ['check_a1 [1, 3]']

    calls = []
    with pytest.raises(ValidationError) as exc_info:
        Model(a=[5, 10])
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': ('a',),
            'msg': 'Value error, a2 broken',
            'input': [6, 10],
            'ctx': {'error': 'a2 broken'},
        }
    ]
    assert calls == ['check_a1 [5, 10]', 'check_a2 [6, 10]']


@pytest.fixture(scope='session', name='ValidateAssignmentModel')
def validate_assignment_model_fixture():
    class ValidateAssignmentModel(BaseModel):
        a: int = 4
        b: str = ...
        c: int = 0

        @field_validator('b')
        @classmethod
        def b_length(cls, v, info):
            values = info.data
            if 'a' in values and len(v) < values['a']:
                raise ValueError('b too short')
            return v

        @field_validator('c')
        @classmethod
        def double_c(cls, v: Any):
            return v * 2

        model_config = ConfigDict(validate_assignment=True, extra='allow')

    return ValidateAssignmentModel


def test_validating_assignment_ok(ValidateAssignmentModel):
    p = ValidateAssignmentModel(b='hello')
    assert p.b == 'hello'


def test_validating_assignment_fail(ValidateAssignmentModel):
    with pytest.raises(ValidationError):
        ValidateAssignmentModel(a=10, b='hello')

    p = ValidateAssignmentModel(b='hello')
    with pytest.raises(ValidationError):
        p.b = 'x'


def test_validating_assignment_value_change(ValidateAssignmentModel):
    p = ValidateAssignmentModel(b='hello', c=2)
    assert p.c == 4

    p = ValidateAssignmentModel(b='hello')
    assert p.c == 0
    p.c = 3
    assert p.c == 6


def test_validating_assignment_extra(ValidateAssignmentModel):
    p = ValidateAssignmentModel(b='hello', extra_field=1.23)
    assert p.extra_field == 1.23

    p = ValidateAssignmentModel(b='hello')
    p.extra_field = 1.23
    assert p.extra_field == 1.23
    p.extra_field = 'bye'
    assert p.extra_field == 'bye'


def test_validating_assignment_dict(ValidateAssignmentModel):
    with pytest.raises(ValidationError) as exc_info:
        ValidateAssignmentModel(a='x', b='xx')
    assert exc_info.value.errors() == [
        {
            'type': 'int_parsing',
            'loc': ('a',),
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'x',
        }
    ]


def test_validating_assignment_values_dict():
    class ModelOne(BaseModel):
        a: int

    class ModelTwo(BaseModel):
        m: ModelOne
        b: int

        @field_validator('b')
        @classmethod
        def validate_b(cls, b, info: FieldValidationInfo):
            if 'm' in info.data:
                return b + info.data['m'].a  # this fails if info.data['m'] is a dict
            else:
                return b

        model_config = ConfigDict(validate_assignment=True)

    model = ModelTwo(m=ModelOne(a=1), b=2)
    assert model.b == 3
    model.b = 3
    assert model.b == 4


def test_validate_multiple():
    class Model(BaseModel):
        a: str
        b: str

        @field_validator('a', 'b')
        @classmethod
        def check_a_and_b(cls, v: Any, info: FieldValidationInfo) -> Any:
            if len(v) < 4:
                field = cls.model_fields[info.field_name]
                raise AssertionError(f'{field.alias or info.field_name} is too short')
            return v + 'x'

    assert Model(a='1234', b='5678').model_dump() == {'a': '1234x', 'b': '5678x'}

    with pytest.raises(ValidationError) as exc_info:
        Model(a='x', b='x')
    assert exc_info.value.errors() == [
        {
            'type': 'assertion_error',
            'loc': ('a',),
            'msg': 'Assertion failed, a is too short',
            'input': 'x',
            'ctx': {'error': 'a is too short'},
        },
        {
            'type': 'assertion_error',
            'loc': ('b',),
            'msg': 'Assertion failed, b is too short',
            'input': 'x',
            'ctx': {'error': 'b is too short'},
        },
    ]


def test_classmethod():
    class Model(BaseModel):
        a: str

        @field_validator('a')
        @classmethod
        def check_a(cls, v: Any):
            assert cls is Model
            return v

    m = Model(a='this is foobar good')
    assert m.a == 'this is foobar good'
    m.check_a('x')


def test_use_bare():
    with pytest.raises(TypeError, match='validators should be used with fields'):

        class Model(BaseModel):
            a: str

            with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

                @validator
                def checker(cls, v):
                    return v


def test_use_bare_field_validator():
    with pytest.raises(TypeError, match='field_validators should be used with fields'):

        class Model(BaseModel):
            a: str

            @field_validator
            def checker(cls, v):
                return v


def test_use_no_fields():
    with pytest.raises(TypeError, match=re.escape("validator() missing 1 required positional argument: '__field'")):

        class Model(BaseModel):
            a: str

            with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

                @validator()
                def checker(cls, v):
                    return v


def test_use_no_fields_field_validator():
    with pytest.raises(
        TypeError, match=re.escape("field_validator() missing 1 required positional argument: '__field'")
    ):

        class Model(BaseModel):
            a: str

            @field_validator()
            def checker(cls, v):
                return v


def test_validator_bad_fields_throws_configerror():
    """
    Attempts to create a validator with fields set as a list of strings,
    rather than just multiple string args. Expects ConfigError to be raised.
    """
    with pytest.raises(TypeError, match='validator fields should be passed as separate string args.'):

        class Model(BaseModel):
            a: str
            b: str

            with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

                @validator(['a', 'b'])
                def check_fields(cls, v):
                    return v


def test_field_validator_bad_fields_throws_configerror():
    """
    Attempts to create a validator with fields set as a list of strings,
    rather than just multiple string args. Expects ConfigError to be raised.
    """
    with pytest.raises(TypeError, match='field_validator fields should be passed as separate string args.'):

        class Model(BaseModel):
            a: str
            b: str

            @field_validator(['a', 'b'])
            def check_fields(cls, v):
                return v


def test_validate_always():
    check_calls = 0

    class Model(BaseModel):
        a: str = None

        with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

            @validator('a', pre=True, always=True)
            @classmethod
            def check_a(cls, v: Any):
                nonlocal check_calls
                check_calls += 1
                return v or 'xxx'

    assert Model().a == 'xxx'
    assert check_calls == 1
    assert Model(a='y').a == 'y'
    assert check_calls == 2


def test_field_validator_validate_default():
    check_calls = 0

    class Model(BaseModel):
        a: str = Field(None, validate_default=True)

        @field_validator('a', mode='before')
        @classmethod
        def check_a(cls, v: Any):
            nonlocal check_calls
            check_calls += 1
            return v or 'xxx'

    assert Model().a == 'xxx'
    assert check_calls == 1
    assert Model(a='y').a == 'y'
    assert check_calls == 2


def test_validate_always_on_inheritance():
    check_calls = 0

    class ParentModel(BaseModel):
        a: str = None

    class Model(ParentModel):
        with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

            @validator('a', pre=True, always=True)
            @classmethod
            def check_a(cls, v: Any):
                nonlocal check_calls
                check_calls += 1
                return v or 'xxx'

    assert Model().a == 'xxx'
    assert check_calls == 1
    assert Model(a='y').a == 'y'
    assert check_calls == 2


def test_field_validator_validate_default_on_inheritance():
    check_calls = 0

    class ParentModel(BaseModel):
        a: str = Field(None, validate_default=True)

    class Model(ParentModel):
        @field_validator('a', mode='before')
        @classmethod
        def check_a(cls, v: Any):
            nonlocal check_calls
            check_calls += 1
            return v or 'xxx'

    assert Model().a == 'xxx'
    assert check_calls == 1
    assert Model(a='y').a == 'y'
    assert check_calls == 2


def test_validate_not_always():
    check_calls = 0

    class Model(BaseModel):
        a: Optional[str] = None

        @field_validator('a', mode='before')
        @classmethod
        def check_a(cls, v: Any):
            nonlocal check_calls
            check_calls += 1
            return v or 'xxx'

    assert Model().a is None
    assert check_calls == 0
    assert Model(a='y').a == 'y'
    assert check_calls == 1


def test_wildcard_validators():
    calls: list[tuple[str, Any]] = []

    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            a: str
            b: int

            @validator('a')
            def check_a(cls, v: Any) -> Any:
                calls.append(('check_a', v))
                return v

            @validator('*')
            def check_all(cls, v: Any) -> Any:
                calls.append(('check_all', v))
                return v

    assert Model(a='abc', b='123').model_dump() == dict(a='abc', b=123)
    assert calls == [('check_a', 'abc'), ('check_all', 'abc'), ('check_all', 123)]


def test_wildcard_validator_error():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            a: str
            b: str

            @validator('*')
            def check_all(cls, v: Any) -> Any:
                if 'foobar' not in v:
                    raise ValueError('"foobar" not found in a')
                return v

    assert Model(a='foobar a', b='foobar b').b == 'foobar b'

    with pytest.raises(ValidationError) as exc_info:
        Model(a='snap')

    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': ('a',),
            'msg': 'Value error, "foobar" not found in a',
            'input': 'snap',
            'ctx': {'error': '"foobar" not found in a'},
        },
        {'type': 'missing', 'loc': ('b',), 'msg': 'Field required', 'input': {'a': 'snap'}},
    ]


def test_invalid_field():
    msg = (
        r'Validators defined with incorrect fields:'
        r' tests.test_validators.test_invalid_field.<locals>.Model:\d+.check_b'
        r" \(use check_fields=False if you're inheriting from the model and intended this\)"
    )
    with pytest.raises(errors.PydanticUserError, match=msg):

        class Model(BaseModel):
            a: str

            @field_validator('b')
            def check_b(cls, v: Any):
                return v


def test_validate_child():
    class Parent(BaseModel):
        a: str

    class Child(Parent):
        @field_validator('a')
        @classmethod
        def check_a(cls, v: Any):
            if 'foobar' not in v:
                raise ValueError('"foobar" not found in a')
            return v

    assert Parent(a='this is not a child').a == 'this is not a child'
    assert Child(a='this is foobar good').a == 'this is foobar good'
    with pytest.raises(ValidationError):
        Child(a='snap')


def test_validate_child_extra():
    class Parent(BaseModel):
        a: str

        @field_validator('a')
        @classmethod
        def check_a_one(cls, v: Any):
            if 'foobar' not in v:
                raise ValueError('"foobar" not found in a')
            return v

    class Child(Parent):
        @field_validator('a')
        @classmethod
        def check_a_two(cls, v: Any):
            return v.upper()

    assert Parent(a='this is foobar good').a == 'this is foobar good'
    assert Child(a='this is foobar good').a == 'THIS IS FOOBAR GOOD'
    with pytest.raises(ValidationError):
        Child(a='snap')


def test_validate_child_all():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Parent(BaseModel):
            a: str

        class Child(Parent):
            @validator('*')
            @classmethod
            def check_a(cls, v: Any):
                if 'foobar' not in v:
                    raise ValueError('"foobar" not found in a')
                return v

        assert Parent(a='this is not a child').a == 'this is not a child'
        assert Child(a='this is foobar good').a == 'this is foobar good'
        with pytest.raises(ValidationError):
            Child(a='snap')


def test_validate_parent():
    class Parent(BaseModel):
        a: str

        @field_validator('a')
        @classmethod
        def check_a(cls, v: Any):
            if 'foobar' not in v:
                raise ValueError('"foobar" not found in a')
            return v

    class Child(Parent):
        pass

    assert Parent(a='this is foobar good').a == 'this is foobar good'
    assert Child(a='this is foobar good').a == 'this is foobar good'
    with pytest.raises(ValidationError):
        Parent(a='snap')
    with pytest.raises(ValidationError):
        Child(a='snap')


def test_validate_parent_all():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Parent(BaseModel):
            a: str

            @validator('*')
            @classmethod
            def check_a(cls, v: Any):
                if 'foobar' not in v:
                    raise ValueError('"foobar" not found in a')
                return v

        class Child(Parent):
            pass

        assert Parent(a='this is foobar good').a == 'this is foobar good'
        assert Child(a='this is foobar good').a == 'this is foobar good'
        with pytest.raises(ValidationError):
            Parent(a='snap')
        with pytest.raises(ValidationError):
            Child(a='snap')


def test_inheritance_keep():
    class Parent(BaseModel):
        a: int

        @field_validator('a')
        @classmethod
        def add_to_a(cls, v: Any):
            return v + 1

    class Child(Parent):
        pass

    assert Child(a=0).a == 1


def test_inheritance_replace():
    """We promise that if you add a validator
    with the same _function_ name as an existing validator
    it replaces the existing validator and is run instead of it.
    """

    class Parent(BaseModel):
        a: List[str]

        @field_validator('a')
        @classmethod
        def parent_val_before(cls, v: List[str]):
            v.append('parent before')
            return v

        @field_validator('a')
        @classmethod
        def val(cls, v: List[str]):
            v.append('parent')
            return v

        @field_validator('a')
        @classmethod
        def parent_val_after(cls, v: List[str]):
            v.append('parent after')
            return v

    class Child(Parent):
        @field_validator('a')
        @classmethod
        def child_val_before(cls, v: List[str]):
            v.append('child before')
            return v

        @field_validator('a')
        @classmethod
        def val(cls, v: List[str]):
            v.append('child')
            return v

        @field_validator('a')
        @classmethod
        def child_val_after(cls, v: List[str]):
            v.append('child after')
            return v

    assert Parent(a=[]).a == ['parent before', 'parent', 'parent after']
    assert Child(a=[]).a == ['parent before', 'child', 'parent after', 'child before', 'child after']


def test_inheritance_replace_root_validator():
    """
    We promise that if you add a validator
    with the same _function_ name as an existing validator
    it replaces the existing validator and is run instead of it.
    """

    class Parent(BaseModel):
        a: List[str]

        @root_validator(skip_on_failure=True)
        def parent_val_before(cls, values: Dict[str, Any]):
            values['a'].append('parent before')
            return values

        @root_validator(skip_on_failure=True)
        def val(cls, values: Dict[str, Any]):
            values['a'].append('parent')
            return values

        @root_validator(skip_on_failure=True)
        def parent_val_after(cls, values: Dict[str, Any]):
            values['a'].append('parent after')
            return values

    class Child(Parent):
        @root_validator(skip_on_failure=True)
        def child_val_before(cls, values: Dict[str, Any]):
            values['a'].append('child before')
            return values

        @root_validator(skip_on_failure=True)
        def val(cls, values: Dict[str, Any]):
            values['a'].append('child')
            return values

        @root_validator(skip_on_failure=True)
        def child_val_after(cls, values: Dict[str, Any]):
            values['a'].append('child after')
            return values

    assert Parent(a=[]).a == ['parent before', 'parent', 'parent after']
    assert Child(a=[]).a == ['parent before', 'child', 'parent after', 'child before', 'child after']


def test_validation_each_item():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            foobar: Dict[int, int]

            @validator('foobar', each_item=True)
            @classmethod
            def check_foobar(cls, v: Any):
                return v + 1

    assert Model(foobar={1: 1}).foobar == {1: 2}


def test_validation_each_item_nullable():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            foobar: Optional[List[int]]

            @validator('foobar', each_item=True)
            @classmethod
            def check_foobar(cls, v: Any):
                return v + 1

    assert Model(foobar=[1]).foobar == [2]


def test_validation_each_item_one_sublevel():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            foobar: List[Tuple[int, int]]

            @validator('foobar', each_item=True)
            @classmethod
            def check_foobar(cls, v: Tuple[int, int]) -> Tuple[int, int]:
                v1, v2 = v
                assert v1 == v2
                return v

    assert Model(foobar=[(1, 1), (2, 2)]).foobar == [(1, 1), (2, 2)]


def test_key_validation():
    class Model(BaseModel):
        foobar: Dict[int, int]

        @field_validator('foobar')
        @classmethod
        def check_foobar(cls, value):
            return {k + 1: v + 1 for k, v in value.items()}

    assert Model(foobar={1: 1}).foobar == {2: 2}


def test_validator_always_optional():
    check_calls = 0

    class Model(BaseModel):
        a: Optional[str] = None

        with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

            @validator('a', pre=True, always=True)
            @classmethod
            def check_a(cls, v: Any):
                nonlocal check_calls
                check_calls += 1
                return v or 'default value'

    assert Model(a='y').a == 'y'
    assert check_calls == 1
    assert Model().a == 'default value'
    assert check_calls == 2


def test_field_validator_validate_default_optional():
    check_calls = 0

    class Model(BaseModel):
        a: Optional[str] = Field(None, validate_default=True)

        @field_validator('a', mode='before')
        @classmethod
        def check_a(cls, v: Any):
            nonlocal check_calls
            check_calls += 1
            return v or 'default value'

    assert Model(a='y').a == 'y'
    assert check_calls == 1
    assert Model().a == 'default value'
    assert check_calls == 2


def test_validator_always_pre():
    check_calls = 0

    class Model(BaseModel):
        a: str = None

        with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

            @validator('a', pre=True, always=True)
            @classmethod
            def check_a(cls, v: Any):
                nonlocal check_calls
                check_calls += 1
                return v or 'default value'

    assert Model(a='y').a == 'y'
    assert Model().a == 'default value'
    assert check_calls == 2


def test_field_validator_validate_default_pre():
    check_calls = 0

    class Model(BaseModel):
        a: str = Field(None, validate_default=True)

        @field_validator('a', mode='before')
        @classmethod
        def check_a(cls, v: Any):
            nonlocal check_calls
            check_calls += 1
            return v or 'default value'

    assert Model(a='y').a == 'y'
    assert Model().a == 'default value'
    assert check_calls == 2


def test_validator_always_post():
    class Model(BaseModel):
        # NOTE: Unlike in v1, you can't replicate the behavior of only applying defined validators and not standard
        # field validation. This is why I've set the default to '' instead of None.
        # But, I think this is a good thing, and I don't think we should try to support this.
        a: str = ''

        with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

            @validator('a', always=True)
            @classmethod
            def check_a(cls, v: Any):
                return v or 'default value'

    assert Model(a='y').a == 'y'
    assert Model().a == 'default value'


def test_field_validator_validate_default_post():
    class Model(BaseModel):
        a: str = Field('', validate_default=True)

        @field_validator('a')
        @classmethod
        def check_a(cls, v: Any):
            return v or 'default value'

    assert Model(a='y').a == 'y'
    assert Model().a == 'default value'


def test_validator_always_post_optional():
    class Model(BaseModel):
        a: Optional[str] = None

        with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

            @validator('a', pre=True, always=True)
            @classmethod
            def check_a(cls, v: Any):
                return 'default value' if v is None else v

    assert Model(a='y').a == 'y'
    assert Model().a == 'default value'


def test_field_validator_validate_default_post_optional():
    class Model(BaseModel):
        a: Optional[str] = Field(None, validate_default=True)

        @field_validator('a', mode='before')
        @classmethod
        def check_a(cls, v: Any):
            return v or 'default value'

    assert Model(a='y').a == 'y'
    assert Model().a == 'default value'


def test_datetime_validator():
    check_calls = 0

    class Model(BaseModel):
        d: datetime = None

        with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

            @validator('d', pre=True, always=True)
            @classmethod
            def check_d(cls, v: Any):
                nonlocal check_calls
                check_calls += 1
                return v or datetime(2032, 1, 1)

    assert Model(d='2023-01-01T00:00:00').d == datetime(2023, 1, 1)
    assert check_calls == 1
    assert Model().d == datetime(2032, 1, 1)
    assert check_calls == 2
    assert Model(d=datetime(2023, 1, 1)).d == datetime(2023, 1, 1)
    assert check_calls == 3


def test_datetime_field_validator():
    check_calls = 0

    class Model(BaseModel):
        d: datetime = Field(None, validate_default=True)

        @field_validator('d', mode='before')
        @classmethod
        def check_d(cls, v: Any):
            nonlocal check_calls
            check_calls += 1
            return v or datetime(2032, 1, 1)

    assert Model(d='2023-01-01T00:00:00').d == datetime(2023, 1, 1)
    assert check_calls == 1
    assert Model().d == datetime(2032, 1, 1)
    assert check_calls == 2
    assert Model(d=datetime(2023, 1, 1)).d == datetime(2023, 1, 1)
    assert check_calls == 3


def test_pre_called_once():
    check_calls = 0

    class Model(BaseModel):
        a: Tuple[int, int, int]

        @field_validator('a', mode='before')
        @classmethod
        def check_a(cls, v: Any):
            nonlocal check_calls
            check_calls += 1
            return v

    assert Model(a=['1', '2', '3']).a == (1, 2, 3)
    assert check_calls == 1


def test_assert_raises_validation_error():
    class Model(BaseModel):
        a: str

        @field_validator('a')
        @classmethod
        def check_a(cls, v: Any):
            assert v == 'a', 'invalid a'
            return v

    Model(a='a')

    with pytest.raises(ValidationError) as exc_info:
        Model(a='snap')
    injected_by_pytest = "assert 'snap' == 'a'\n  - a\n  + snap"
    assert exc_info.value.errors() == [
        {
            'type': 'assertion_error',
            'loc': ('a',),
            'msg': f'Assertion failed, invalid a\n{injected_by_pytest}',
            'input': 'snap',
            'ctx': {'error': "invalid a\nassert 'snap' == 'a'\n  - a\n  + snap"},
        }
    ]


def test_root_validator():
    root_val_values: List[Dict[str, Any]] = []

    class Model(BaseModel):
        a: int = 1
        b: str
        c: str

        @field_validator('b')
        @classmethod
        def repeat_b(cls, v: Any):
            return v * 2

        @root_validator(skip_on_failure=True)
        def example_root_validator(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            root_val_values.append(values)
            if 'snap' in values.get('b', ''):
                raise ValueError('foobar')
            return dict(values, b='changed')

        @root_validator(skip_on_failure=True)
        def example_root_validator2(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            root_val_values.append(values)
            if 'snap' in values.get('c', ''):
                raise ValueError('foobar2')
            return dict(values, c='changed')

    assert Model(a='123', b='bar', c='baz').model_dump() == {'a': 123, 'b': 'changed', 'c': 'changed'}

    with pytest.raises(ValidationError) as exc_info:
        Model(b='snap dragon', c='snap dragon2')
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': (),
            'msg': 'Value error, foobar',
            'input': {'b': 'snap dragon', 'c': 'snap dragon2'},
            'ctx': {'error': 'foobar'},
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        Model(a='broken', b='bar', c='baz')
    assert exc_info.value.errors() == [
        {
            'type': 'int_parsing',
            'loc': ('a',),
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'broken',
        }
    ]

    assert root_val_values == [
        {'a': 123, 'b': 'barbar', 'c': 'baz'},
        {'a': 123, 'b': 'changed', 'c': 'baz'},
        {'a': 1, 'b': 'snap dragonsnap dragon', 'c': 'snap dragon2'},
    ]


def test_root_validator_subclass():
    """
    https://github.com/pydantic/pydantic/issues/5388
    """

    class Parent(BaseModel):
        x: int
        expected: Any

        @root_validator(skip_on_failure=True)
        @classmethod
        def root_val(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            assert cls is values['expected']
            return values

    class Child1(Parent):
        pass

    class Child2(Parent):
        @root_validator(skip_on_failure=True)
        @classmethod
        def root_val(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            assert cls is Child2
            values['x'] = values['x'] * 2
            return values

    class Child3(Parent):
        @classmethod
        def root_val(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            assert cls is Child3
            values['x'] = values['x'] * 3
            return values

    assert Parent(x=1, expected=Parent).x == 1
    assert Child1(x=1, expected=Child1).x == 1
    assert Child2(x=1, expected=Child2).x == 2
    assert Child3(x=1, expected=Child3).x == 3


def test_root_validator_pre():
    root_val_values: List[Dict[str, Any]] = []

    class Model(BaseModel):
        a: int = 1
        b: str

        @field_validator('b')
        @classmethod
        def repeat_b(cls, v: Any):
            return v * 2

        @root_validator(pre=True)
        def root_validator(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            root_val_values.append(values)
            if 'snap' in values.get('b', ''):
                raise ValueError('foobar')
            return {'a': 42, 'b': 'changed'}

    assert Model(a='123', b='bar').model_dump() == {'a': 42, 'b': 'changedchanged'}

    with pytest.raises(ValidationError) as exc_info:
        Model(b='snap dragon')

    assert root_val_values == [{'a': '123', 'b': 'bar'}, {'b': 'snap dragon'}]
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': (),
            'msg': 'Value error, foobar',
            'input': {'b': 'snap dragon'},
            'ctx': {'error': 'foobar'},
        }
    ]


def test_root_validator_types():
    root_val_values: Optional[Tuple[Type[BaseModel], Dict[str, Any]]] = None

    class Model(BaseModel):
        a: int = 1
        b: str

        @root_validator(skip_on_failure=True)
        def root_validator(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal root_val_values
            root_val_values = cls, values
            return values

        model_config = ConfigDict(extra='allow')

    assert Model(b='bar', c='wobble').model_dump() == {'a': 1, 'b': 'bar', 'c': 'wobble'}

    assert root_val_values == (Model, {'a': 1, 'b': 'bar', 'c': 'wobble'})


def test_root_validator_returns_none_exception():
    class Model(BaseModel):
        a: int = 1

        @root_validator(skip_on_failure=True)
        def root_validator_repeated(cls, values):
            return None

    with pytest.raises(
        TypeError,
        match=r"(:?__dict__ must be set to a dictionary, not a 'NoneType')|(:?setting dictionary to a non-dict)",
    ):
        Model()


def reusable_validator(num: int) -> int:
    return num * 2


def test_reuse_global_validators():
    class Model(BaseModel):
        x: int
        y: int

        double_x = field_validator('x')(reusable_validator)
        double_y = field_validator('y')(reusable_validator)

    assert dict(Model(x=1, y=1)) == {'x': 2, 'y': 2}


@pytest.mark.parametrize('validator_classmethod,root_validator_classmethod', product(*[[True, False]] * 2))
def test_root_validator_classmethod(validator_classmethod, root_validator_classmethod):
    root_val_values = []

    class Model(BaseModel):
        a: int = 1
        b: str

        def repeat_b(cls, v: Any):
            return v * 2

        if validator_classmethod:
            repeat_b = classmethod(repeat_b)
        repeat_b = field_validator('b')(repeat_b)

        def example_root_validator(cls, values):
            root_val_values.append(values)
            if 'snap' in values.get('b', ''):
                raise ValueError('foobar')
            return dict(values, b='changed')

        if root_validator_classmethod:
            example_root_validator = classmethod(example_root_validator)
        example_root_validator = root_validator(skip_on_failure=True)(example_root_validator)

    assert Model(a='123', b='bar').model_dump() == {'a': 123, 'b': 'changed'}

    with pytest.raises(ValidationError) as exc_info:
        Model(b='snap dragon')
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': (),
            'msg': 'Value error, foobar',
            'input': {'b': 'snap dragon'},
            'ctx': {'error': 'foobar'},
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        Model(a='broken', b='bar')
    assert exc_info.value.errors() == [
        {
            'type': 'int_parsing',
            'loc': ('a',),
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'input': 'broken',
        }
    ]

    assert root_val_values == [{'a': 123, 'b': 'barbar'}, {'a': 1, 'b': 'snap dragonsnap dragon'}]


def test_assignment_validator_cls():
    validator_calls = 0

    class Model(BaseModel):
        name: str

        model_config = ConfigDict(validate_assignment=True)

        @field_validator('name')
        @classmethod
        def check_foo(cls, value):
            nonlocal validator_calls
            validator_calls += 1
            assert cls == Model
            return value

    m = Model(name='hello')
    m.name = 'goodbye'
    assert validator_calls == 2


@pytest.mark.xfail(
    sys.version_info[:2] == (3, 8), reason='https://github.com/python/cpython/issues/103592', strict=False
)
def test_literal_validator():
    class Model(BaseModel):
        a: Literal['foo']

    Model(a='foo')

    with pytest.raises(ValidationError) as exc_info:
        Model(a='nope')
    assert exc_info.value.errors() == [
        {
            'type': 'literal_error',
            'loc': ('a',),
            'msg': "Input should be 'foo'",
            'input': 'nope',
            'ctx': {'expected': "'foo'"},
        }
    ]


def test_literal_validator_str_enum():
    class Bar(str, Enum):
        FIZ = 'fiz'
        FUZ = 'fuz'

    class Foo(BaseModel):
        bar: Bar
        barfiz: Literal[Bar.FIZ]
        fizfuz: Literal[Bar.FIZ, Bar.FUZ]

    my_foo = Foo.model_validate({'bar': 'fiz', 'barfiz': 'fiz', 'fizfuz': 'fiz'})
    assert my_foo.bar is Bar.FIZ
    # TODO: this doesn't pass, `my_foo.barfiz == 'fiz'`
    # Is this an intentional behavior change?
    assert my_foo.barfiz is Bar.FIZ
    assert my_foo.fizfuz is Bar.FIZ

    my_foo = Foo.model_validate({'bar': 'fiz', 'barfiz': 'fiz', 'fizfuz': 'fuz'})
    assert my_foo.bar is Bar.FIZ
    assert my_foo.barfiz is Bar.FIZ
    assert my_foo.fizfuz is Bar.FUZ


@pytest.mark.xfail(
    sys.version_info[:2] == (3, 8), reason='https://github.com/python/cpython/issues/103592', strict=False
)
def test_nested_literal_validator():
    L1 = Literal['foo']
    L2 = Literal['bar']

    class Model(BaseModel):
        a: Literal[L1, L2]

    Model(a='foo')

    with pytest.raises(ValidationError) as exc_info:
        Model(a='nope')
    assert exc_info.value.errors() == [
        {
            'type': 'literal_error',
            'loc': ('a',),
            'msg': "Input should be 'foo' or 'bar'",
            'input': 'nope',
            'ctx': {'expected': "'foo' or 'bar'"},
        }
    ]


def test_union_literal_with_constraints():
    class Model(BaseModel, validate_assignment=True):
        x: Union[Literal[42], Literal['pika']] = Field(frozen=True)

    m = Model(x=42)
    with pytest.raises(ValidationError) as exc_info:
        m.x += 1
    assert exc_info.value.errors() == [{'input': 43, 'loc': ('x',), 'msg': 'Field is frozen', 'type': 'frozen_field'}]


def test_field_that_is_being_validated_is_excluded_from_validator_values():
    check_values = MagicMock()

    class Model(BaseModel):
        foo: str
        bar: str = Field(alias='pika')
        baz: str

        model_config = ConfigDict(validate_assignment=True)

        @field_validator('foo')
        @classmethod
        def validate_foo(cls, v: Any, info: FieldValidationInfo) -> Any:
            check_values({**info.data})
            return v

        @field_validator('bar')
        @classmethod
        def validate_bar(cls, v: Any, info: FieldValidationInfo) -> Any:
            check_values({**info.data})
            return v

    model = Model(foo='foo_value', pika='bar_value', baz='baz_value')
    check_values.reset_mock()

    assert list(dict(model).items()) == [('foo', 'foo_value'), ('bar', 'bar_value'), ('baz', 'baz_value')]

    model.foo = 'new_foo_value'
    check_values.assert_called_once_with({'bar': 'bar_value', 'baz': 'baz_value'})
    check_values.reset_mock()

    model.bar = 'new_bar_value'
    check_values.assert_called_once_with({'foo': 'new_foo_value', 'baz': 'baz_value'})

    # ensure field order is the same
    assert list(dict(model).items()) == [('foo', 'new_foo_value'), ('bar', 'new_bar_value'), ('baz', 'baz_value')]


def test_exceptions_in_field_validators_restore_original_field_value():
    class Model(BaseModel):
        foo: str

        model_config = ConfigDict(validate_assignment=True)

        @field_validator('foo')
        @classmethod
        def validate_foo(cls, v: Any):
            if v == 'raise_exception':
                raise RuntimeError('test error')
            return v

    model = Model(foo='foo')
    with pytest.raises(RuntimeError, match='test error'):
        model.foo = 'raise_exception'
    assert model.foo == 'foo'


def test_overridden_root_validators():
    validate_stub = MagicMock()

    class A(BaseModel):
        x: str

        @root_validator(pre=True)
        def pre_root(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            validate_stub('A', 'pre')
            return values

        @root_validator(pre=False, skip_on_failure=True)
        def post_root(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            validate_stub('A', 'post')
            return values

    class B(A):
        @root_validator(pre=True)
        def pre_root(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            validate_stub('B', 'pre')
            return values

        @root_validator(pre=False, skip_on_failure=True)
        def post_root(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            validate_stub('B', 'post')
            return values

    A(x='pika')
    assert validate_stub.call_args_list == [[('A', 'pre'), {}], [('A', 'post'), {}]]

    validate_stub.reset_mock()

    B(x='pika')
    assert validate_stub.call_args_list == [[('B', 'pre'), {}], [('B', 'post'), {}]]


def test_validating_assignment_pre_root_validator_fail():
    class Model(BaseModel):
        current_value: float = Field(..., alias='current')
        max_value: float

        model_config = ConfigDict(validate_assignment=True)

        @root_validator(pre=True)
        def values_are_not_string(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            if any(isinstance(x, str) for x in values.values()):
                raise ValueError('values cannot be a string')
            return values

    m = Model(current=100, max_value=200)
    with pytest.raises(ValidationError) as exc_info:
        m.current_value = '100'
    assert exc_info.value.errors() == [
        {
            'type': 'value_error',
            'loc': (),
            'msg': 'Value error, values cannot be a string',
            'input': {'current_value': '100', 'max_value': 200.0},
            'ctx': {'error': 'values cannot be a string'},
        }
    ]


@pytest.mark.parametrize(
    'kwargs',
    [
        {'skip_on_failure': False},
        {'skip_on_failure': False, 'pre': False},
        {'pre': False},
    ],
)
def test_root_validator_skip_on_failure_invalid(kwargs: Dict[str, Any]):
    with pytest.raises(TypeError, match='MUST specify `skip_on_failure=True`'):

        class Model(BaseModel):
            @root_validator(**kwargs)
            def root_val(cls, values: Dict[str, Any]) -> Dict[str, Any]:
                return values


@pytest.mark.parametrize(
    'kwargs',
    [
        {'skip_on_failure': True},
        {'skip_on_failure': True, 'pre': False},
        {'skip_on_failure': False, 'pre': True},
        {'pre': True},
    ],
)
def test_root_validator_skip_on_failure_valid(kwargs: Dict[str, Any]):
    class Model(BaseModel):
        @root_validator(**kwargs)
        def root_val(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            return values


def test_root_validator_many_values_change():
    """It should run root_validator on assignment and update ALL concerned fields"""

    class Rectangle(BaseModel):
        width: float
        height: float
        area: Optional[float] = None

        model_config = ConfigDict(validate_assignment=True)

        @root_validator(skip_on_failure=True)
        def set_area(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            values['area'] = values['width'] * values['height']
            return values

    r = Rectangle(width=1, height=1)
    assert r.area == 1
    r.height = 5
    assert r.area == 5


V1_VALIDATOR_DEPRECATION_MATCH = r'Pydantic V1 style `@validator` validators are deprecated'


def _get_source_line(filename: str, lineno: int) -> str:
    with open(filename) as f:
        for _ in range(lineno - 1):
            f.readline()
        return f.readline()


def test_v1_validator_deprecated():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH) as w:

        class Point(BaseModel):
            y: int
            x: int

            @validator('x')
            @classmethod
            def check_x(cls, x: int, values: Dict[str, Any]) -> int:
                assert x * 2 == values['y']
                return x

    assert Point(x=1, y=2).model_dump() == {'x': 1, 'y': 2}

    warnings = w.list
    assert len(warnings) == 1
    w = warnings[0]
    # check that we got stacklevel correct
    # if this fails you need to edit the stacklevel
    # parameter to warnings.warn in _decorators.py
    assert w.filename == __file__
    source = _get_source_line(w.filename, w.lineno)
    # the reported location varies slightly from 3.7 to 3.11
    assert 'check_x' in source or "@validator('x')" in source


def test_info_field_name_data_before():
    """
    Test accessing info.field_name and info.data
    We only test the `before` validator because they
    all share the same implementation.
    """

    class Model(BaseModel):
        a: str
        b: str

        @field_validator('b', mode='before')
        @classmethod
        def check_a(cls, v: Any, info: FieldValidationInfo) -> Any:
            assert v == b'but my barbaz is better'
            assert info.field_name == 'b'
            assert info.data == {'a': 'your foobar is good'}
            return 'just kidding!'

    assert Model(a=b'your foobar is good', b=b'but my barbaz is better').b == 'just kidding!'


def test_decorator_proxy():
    """
    Test that our validator decorator allows
    calling the wrapped methods/functions.
    """

    def val(v: int) -> int:
        return v + 1

    class Model(BaseModel):
        x: int

        @field_validator('x')
        @staticmethod
        def val1(v: int) -> int:
            return v + 1

        @field_validator('x')
        @classmethod
        def val2(cls, v: int) -> int:
            return v + 1

        val3 = field_validator('x')(val)

    assert Model.val1(1) == 2
    assert Model.val2(1) == 2
    assert Model.val3(1) == 2


def test_root_validator_self():
    with pytest.raises(TypeError, match=r'`@root_validator` cannot be applied to instance methods'):

        class Model(BaseModel):
            a: int = 1

            @root_validator(skip_on_failure=True)
            def root_validator(self, values: Any) -> Any:
                return values


def test_validator_self():
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):
        with pytest.raises(TypeError, match=r'`@validator` cannot be applied to instance methods'):

            class Model(BaseModel):
                a: int = 1

                @validator('a')
                def check_a(self, values: Any) -> Any:
                    return values


def test_field_validator_self():
    with pytest.raises(TypeError, match=r'`@field_validator` cannot be applied to instance methods'):

        class Model(BaseModel):
            a: int = 1

            @field_validator('a')
            def check_a(self, values: Any) -> Any:
                return values


def test_v1_validator_signature_kwargs_not_allowed() -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):
        with pytest.raises(TypeError, match=r'Unsupported signature for V1 style validator'):

            class Model(BaseModel):
                a: int

                @validator('a')
                def check_a(cls, value: Any, foo: Any) -> Any:
                    ...


def test_v1_validator_signature_kwargs1() -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            a: int
            b: int

            @validator('b')
            def check_b(cls, value: Any, **kwargs: Any) -> Any:
                assert kwargs == {'values': {'a': 1}}
                assert value == 2
                return value + 1

    assert Model(a=1, b=2).model_dump() == {'a': 1, 'b': 3}


def test_v1_validator_signature_kwargs2() -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            a: int
            b: int

            @validator('b')
            def check_b(cls, value: Any, values: Dict[str, Any], **kwargs: Any) -> Any:
                assert kwargs == {}
                assert values == {'a': 1}
                assert value == 2
                return value + 1

    assert Model(a=1, b=2).model_dump() == {'a': 1, 'b': 3}


def test_v1_validator_signature_with_values() -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            a: int
            b: int

            @validator('b')
            def check_b(cls, value: Any, values: Dict[str, Any]) -> Any:
                assert values == {'a': 1}
                assert value == 2
                return value + 1

    assert Model(a=1, b=2).model_dump() == {'a': 1, 'b': 3}


def test_v1_validator_signature_with_values_kw_only() -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            a: int
            b: int

            @validator('b')
            def check_b(cls, value: Any, *, values: Dict[str, Any]) -> Any:
                assert values == {'a': 1}
                assert value == 2
                return value + 1

    assert Model(a=1, b=2).model_dump() == {'a': 1, 'b': 3}


def test_v1_validator_signature_with_field() -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):
        with pytest.raises(TypeError, match=r'The `field` and `config` parameters are not available in Pydantic V2'):

            class Model(BaseModel):
                a: int
                b: int

                @validator('b')
                def check_b(cls, value: Any, field: Any) -> Any:
                    ...


def test_v1_validator_signature_with_config() -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):
        with pytest.raises(TypeError, match=r'The `field` and `config` parameters are not available in Pydantic V2'):

            class Model(BaseModel):
                a: int
                b: int

                @validator('b')
                def check_b(cls, value: Any, config: Any) -> Any:
                    ...


def test_model_config_validate_default():
    class Model(BaseModel):
        x: int = -1

        @field_validator('x')
        @classmethod
        def force_x_positive(cls, v):
            assert v > 0
            return v

    assert Model().x == -1

    class ValidatingModel(Model):
        model_config = ConfigDict(validate_default=True)

    with pytest.raises(ValidationError) as exc_info:
        ValidatingModel()
    assert exc_info.value.errors() == [
        {
            'ctx': {'error': 'assert -1 > 0'},
            'input': -1,
            'loc': ('x',),
            'msg': 'Assertion failed, assert -1 > 0',
            'type': 'assertion_error',
        }
    ]


def partial_val_func1(
    value: int,
    allowed: int,
) -> int:
    assert value == allowed
    return value


def partial_val_func2(
    value: int,
    *,
    allowed: int,
) -> int:
    assert value == allowed
    return value


def partial_values_val_func1(
    value: int,
    values: Dict[str, Any],
    *,
    allowed: int,
) -> int:
    assert isinstance(values, dict)
    assert value == allowed
    return value


def partial_values_val_func2(
    value: int,
    *,
    values: Dict[str, Any],
    allowed: int,
) -> int:
    assert isinstance(values, dict)
    assert value == allowed
    return value


def partial_info_val_func(
    value: int,
    info: FieldValidationInfo,
    *,
    allowed: int,
) -> int:
    assert isinstance(info.data, dict)
    assert value == allowed
    return value


def partial_cls_val_func1(
    cls: Any,
    value: int,
    allowed: int,
    expected_cls: Any,
) -> int:
    assert cls.__name__ == expected_cls
    assert value == allowed
    return value


def partial_cls_val_func2(
    cls: Any,
    value: int,
    *,
    allowed: int,
    expected_cls: Any,
) -> int:
    assert cls.__name__ == expected_cls
    assert value == allowed
    return value


def partial_cls_values_val_func1(
    cls: Any,
    value: int,
    values: Dict[str, Any],
    *,
    allowed: int,
    expected_cls: Any,
) -> int:
    assert cls.__name__ == expected_cls
    assert isinstance(values, dict)
    assert value == allowed
    return value


def partial_cls_values_val_func2(
    cls: Any,
    value: int,
    *,
    values: Dict[str, Any],
    allowed: int,
    expected_cls: Any,
) -> int:
    assert cls.__name__ == expected_cls
    assert isinstance(values, dict)
    assert value == allowed
    return value


def partial_cls_info_val_func(
    cls: Any,
    value: int,
    info: FieldValidationInfo,
    *,
    allowed: int,
    expected_cls: Any,
) -> int:
    assert cls.__name__ == expected_cls
    assert isinstance(info.data, dict)
    assert value == allowed
    return value


@pytest.mark.parametrize(
    'func',
    [
        partial_val_func1,
        partial_val_func2,
        partial_info_val_func,
    ],
)
def test_functools_partial_validator_v2(
    func: Callable[..., Any],
) -> None:
    class Model(BaseModel):
        x: int

        val = field_validator('x')(partial(func, allowed=42))

    Model(x=42)

    with pytest.raises(ValidationError):
        Model(x=123)


@pytest.mark.parametrize(
    'func',
    [
        partial_val_func1,
        partial_val_func2,
        partial_info_val_func,
    ],
)
def test_functools_partialmethod_validator_v2(
    func: Callable[..., Any],
) -> None:
    class Model(BaseModel):
        x: int

        val = field_validator('x')(partialmethod(func, allowed=42))

    Model(x=42)

    with pytest.raises(ValidationError):
        Model(x=123)


@pytest.mark.parametrize(
    'func',
    [
        partial_cls_val_func1,
        partial_cls_val_func2,
        partial_cls_info_val_func,
    ],
)
def test_functools_partialmethod_validator_v2_cls_method(
    func: Callable[..., Any],
) -> None:
    class Model(BaseModel):
        x: int

        # note that you _have_ to wrap your function with classmethod
        # it's partialmethod not us that requires it
        # otherwise it creates a bound instance method
        val = field_validator('x')(partialmethod(classmethod(func), allowed=42, expected_cls='Model'))

    Model(x=42)

    with pytest.raises(ValidationError):
        Model(x=123)


@pytest.mark.parametrize(
    'func',
    [
        partial_val_func1,
        partial_val_func2,
        partial_values_val_func1,
        partial_values_val_func2,
    ],
)
def test_functools_partial_validator_v1(
    func: Callable[..., Any],
) -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            x: int

            val = validator('x')(partial(func, allowed=42))

    Model(x=42)

    with pytest.raises(ValidationError):
        Model(x=123)


@pytest.mark.parametrize(
    'func',
    [
        partial_val_func1,
        partial_val_func2,
        partial_values_val_func1,
        partial_values_val_func2,
    ],
)
def test_functools_partialmethod_validator_v1(
    func: Callable[..., Any],
) -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            x: int

            val = validator('x')(partialmethod(func, allowed=42))

        Model(x=42)

        with pytest.raises(ValidationError):
            Model(x=123)


@pytest.mark.parametrize(
    'func',
    [
        partial_cls_val_func1,
        partial_cls_val_func2,
        partial_cls_values_val_func1,
        partial_cls_values_val_func2,
    ],
)
def test_functools_partialmethod_validator_v1_cls_method(
    func: Callable[..., Any],
) -> None:
    with pytest.warns(DeprecationWarning, match=V1_VALIDATOR_DEPRECATION_MATCH):

        class Model(BaseModel):
            x: int

            # note that you _have_ to wrap your function with classmethod
            # it's partialmethod not us that requires it
            # otherwise it creates a bound instance method
            val = validator('x')(partialmethod(classmethod(func), allowed=42, expected_cls='Model'))

    Model(x=42)

    with pytest.raises(ValidationError):
        Model(x=123)


def test_validator_allow_reuse_inheritance():
    class Parent(BaseModel):
        x: int

        @field_validator('x')
        def val(cls, v: int) -> int:
            return v + 1

    class Child(Parent):
        @field_validator('x')
        def val(cls, v: int) -> int:
            assert v == 1
            v = super().val(v)
            assert v == 2
            return 4

    assert Parent(x=1).model_dump() == {'x': 2}
    assert Child(x=1).model_dump() == {'x': 4}


def test_validator_allow_reuse_same_field():
    with pytest.warns(UserWarning, match='`val_x` overrides an existing Pydantic `@field_validator` decorator'):

        class Model(BaseModel):
            x: int

            @field_validator('x')
            def val_x(cls, v: int) -> int:
                return v + 1

            @field_validator('x')
            def val_x(cls, v: int) -> int:  # noqa: F811
                return v + 2

        assert Model(x=1).model_dump() == {'x': 3}


def test_validator_allow_reuse_different_field_1():
    with pytest.warns(UserWarning, match='`val` overrides an existing Pydantic `@field_validator` decorator'):

        class Model(BaseModel):
            x: int
            y: int

            @field_validator('x')
            def val(cls, v: int) -> int:
                return v + 1

            @field_validator('y')
            def val(cls, v: int) -> int:  # noqa: F811
                return v + 2

    assert Model(x=1, y=2).model_dump() == {'x': 1, 'y': 4}


def test_validator_allow_reuse_different_field_2():
    with pytest.warns(UserWarning, match='`val_x` overrides an existing Pydantic `@field_validator` decorator'):

        def val(cls: Any, v: int) -> int:
            return v + 2

        class Model(BaseModel):
            x: int
            y: int

            @field_validator('x')
            def val_x(cls, v: int) -> int:
                return v + 1

            val_x = field_validator('y')(val)  # noqa: F811

    assert Model(x=1, y=2).model_dump() == {'x': 1, 'y': 4}


def test_validator_allow_reuse_different_field_3():
    with pytest.warns(UserWarning, match='`val_x` overrides an existing Pydantic `@field_validator` decorator'):

        def val1(v: int) -> int:
            return v + 1

        def val2(v: int) -> int:
            return v + 2

        class Model(BaseModel):
            x: int
            y: int

            val_x = field_validator('x')(val1)
            val_x = field_validator('y')(val2)

    assert Model(x=1, y=2).model_dump() == {'x': 1, 'y': 4}


def test_validator_allow_reuse_different_field_4():
    def val(v: int) -> int:
        return v + 1

    class Model(BaseModel):
        x: int
        y: int

        val_x = field_validator('x')(val)
        not_val_x = field_validator('y')(val)

    assert Model(x=1, y=2).model_dump() == {'x': 2, 'y': 3}


def test_root_validator_allow_reuse_same_field():
    with pytest.warns(UserWarning, match='`root_val` overrides an existing Pydantic `@root_validator` decorator'):

        class Model(BaseModel):
            x: int

            @root_validator(skip_on_failure=True)
            def root_val(cls, v: Dict[str, Any]) -> Dict[str, Any]:
                v['x'] += 1
                return v

            @root_validator(skip_on_failure=True)
            def root_val(cls, v: Dict[str, Any]) -> Dict[str, Any]:  # noqa: F811
                v['x'] += 2
                return v

        assert Model(x=1).model_dump() == {'x': 3}


def test_root_validator_allow_reuse_inheritance():
    class Parent(BaseModel):
        x: int

        @root_validator(skip_on_failure=True)
        def root_val(cls, v: Dict[str, Any]) -> Dict[str, Any]:
            v['x'] += 1
            return v

    class Child(Parent):
        @root_validator(skip_on_failure=True)
        def root_val(cls, v: Dict[str, Any]) -> Dict[str, Any]:
            assert v == {'x': 1}
            v = super().root_val(v)
            assert v == {'x': 2}
            return {'x': 4}

    assert Parent(x=1).model_dump() == {'x': 2}
    assert Child(x=1).model_dump() == {'x': 4}


def test_validator_with_underscore_name() -> None:
    """
    https://github.com/pydantic/pydantic/issues/5252
    """

    def f(name: str) -> str:
        return name.lower()

    class Model(BaseModel):
        name: str
        _normalize_name = field_validator('name')(f)

    assert Model(name='Adrian').name == 'adrian'
