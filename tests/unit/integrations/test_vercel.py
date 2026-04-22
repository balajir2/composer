"""Unit tests for the Vercel API client — via pytest-httpx."""

from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.integrations.vercel import VercelClient, VercelEnvVar


async def test_upsert_creates_when_absent(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://api.vercel.com/v10/projects/proj-1/env",
        json={"envs": []},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://api.vercel.com/v10/projects/proj-1/env",
        json={"id": "env-1", "key": "ANTHROPIC_API_KEY"},
    )
    client = VercelClient(api_token="token", project_id="proj-1")
    await client.upsert_env(
        VercelEnvVar(key="ANTHROPIC_API_KEY", value="sk-ant-xxx", target=["production"])
    )


async def test_upsert_patches_when_present(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://api.vercel.com/v10/projects/proj-1/env",
        json={"envs": [{"id": "env-existing", "key": "ANTHROPIC_API_KEY", "value": "old"}]},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="PATCH",
        url="https://api.vercel.com/v9/projects/proj-1/env/env-existing",
        json={"id": "env-existing"},
    )
    client = VercelClient(api_token="token", project_id="proj-1")
    await client.upsert_env(
        VercelEnvVar(key="ANTHROPIC_API_KEY", value="sk-ant-new", target=["production"])
    )


async def test_delete_returns_true_when_present(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://api.vercel.com/v10/projects/proj-1/env",
        json={"envs": [{"id": "env-x", "key": "GAMMA_API_KEY"}]},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="DELETE",
        url="https://api.vercel.com/v9/projects/proj-1/env/env-x",
        json={"id": "env-x"},
    )
    client = VercelClient(api_token="t", project_id="proj-1")
    ok = await client.delete_env("GAMMA_API_KEY")
    assert ok is True


async def test_delete_returns_false_when_absent(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://api.vercel.com/v10/projects/proj-1/env",
        json={"envs": []},
    )
    client = VercelClient(api_token="t", project_id="proj-1")
    ok = await client.delete_env("GAMMA_API_KEY")
    assert ok is False
