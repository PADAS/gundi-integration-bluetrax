# GUNDI-5346: Test Connection action reports invalid credentials when credentials are valid

## Summary

The **Test Connection** (`auth`) action always returned `{"valid_credentials": false, "status_code": 400}` even when the supplied Bluetrax credentials were correct and working.

## Root cause

The `gql` `Client` was initialised with `fetch_schema_from_transport=True` in both `authenticate()` and `get_assets()` ([`app/bluetrax.py`](../app/bluetrax.py)).

When a `gql` session opens with this flag set, the library sends a GraphQL **introspection query** to the server _before_ executing the real query. The Bluetrax GraphQL server (`https://graphql.bluetrax.co.ke/graphql/`) has introspection disabled and responds with **HTTP 400**. That raises `httpx.HTTPStatusError`, which the `action_auth` handler catches and maps to:

```json
{"valid_credentials": false, "status_code": 400}
```

The actual credential query is never sent. Credentials can be perfectly valid and the action will still report failure.

The same issue affects `action_pull_observations`: because that handler does not catch `httpx.HTTPStatusError`, it surfaces as a generic 500 error in the activity log.

## One possible fix

Set `fetch_schema_from_transport=False` on both `Client` instances in `app/bluetrax.py`. This tells `gql` to skip the introspection request and execute the query directly. The trade-off is that `gql` can no longer validate queries against a local schema copy at runtime, but for a production integration that is rarely a concern.

**`authenticate()` — before**

```python
async with Client(
    transport=transport,
    fetch_schema_from_transport=True,
) as session:
    query = gql(
        """
        query selectUsersByUsernamePassword($user_name: String!, $password: String!) {
            selectUsersByUsernamePassword(user_name: $user_name, password: $password) {
                    user_id
                    client_id
                    contact_id
                    client_name
                    __typename
            }
        }
    """
    )
    result = await session.execute(query, variable_values={"user_name": username, "password": password})
```

**`authenticate()` — after**

```python
async with Client(
    transport=transport,
    fetch_schema_from_transport=False,
) as session:
    query = gql(
        """
        query selectUsersByUsernamePassword($user_name: String!, $password: String!) {
            selectUsersByUsernamePassword(user_name: $user_name, password: $password) {
                    user_id
                    client_id
                    contact_id
                    client_name
                    __typename
            }
        }
    """
    )
    result = await session.execute(query, variable_values={"user_name": username, "password": password})
```

The same one-line change applies to `get_assets()`.

## Verification

Called `authenticate()` directly against the live Bluetrax endpoint with known-good credentials after applying the change. The introspection request was no longer sent; the credential query succeeded and returned a valid user record.
