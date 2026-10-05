# Panel application

The application verifies a signed identity token before resolving a directory user and task permissions.

## Interface

Public HTTP uses port 8080; authenticated task callbacks use port 8081. Routing uses the local socket port.
The application exposes `/api/me`, task upload, list, status, events, cancel, approval and bundle download.

## Configuration

See [panel configuration](../README.md). Directory files reload on requests so suspended users lose access immediately.

## Secrets

Only the route-restricted mint credential enters the panel process. Task keys enter the runner container environment.

## Deploy

Build the repository-root context using the parent Dockerfile, then render portal manifests.

## Verify

Run the parent unit suite. Identity signing keys are generated in each test run.

## Rollback

Restore the preceding panel image and directory revision. Retain the data volume until task credentials expire.

## Security notes

An unverified subject, email header or group cannot grant access. Auditors remain read-only when also in another role.
