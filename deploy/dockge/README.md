# Plumbus on Dockge

Plumbus publishes three multi-architecture images to GitHub Container Registry:

- `ghcr.io/cachenetworks/plumbus-backend:latest`
- `ghcr.io/cachenetworks/plumbus-frontend:latest`
- `ghcr.io/cachenetworks/plumbus-edge:latest`

The images are built automatically by `.github/workflows/publish-images.yml` for `linux/amd64` and `linux/arm64` whenever `main` changes. Version tags beginning with `v` also produce matching GHCR tags.

## Dockge

1. Make sure the Docker host can pull the private GHCR packages. Authenticate with a GitHub token that has `read:packages` access.
2. If Dockge itself needs registry credentials, mount the Docker auth directory into the Dockge container, for example `/root/.docker:/root/.docker:ro`, then recreate Dockge.
3. In Dockge choose **+ Compose**, name the stack `plumbus`, and paste `deploy/dockge/compose.yaml`.
4. Add the values from `deploy/dockge/.env.example` to the stack Environment editor. Replace all `CHANGE_ME` values.
5. Deploy the stack.
6. Watch the backend logs. On first run Plumbus prints a one-time first-run setup code.
7. Open `http://YOUR_DOCKER_HOST:8080`. A fresh installation redirects to `/setup`.
8. Enter the setup code and complete Site, Discord, Plex sign-in, Plex server, library and playback configuration.
9. Finish setup and sign in with the configured owner Discord account to bootstrap the first SuperAdmin.

Only the edge container publishes a host port. PostgreSQL, Redis, backend, worker, beat and frontend communicate on the stack network.

## Updating

In Dockge, pull/redeploy the stack. Every app image uses `pull_policy: always`, so a redeploy pulls the newest `latest` images.

For reproducible deployments, replace `:latest` with a published `:vX.Y.Z` tag once releases are being used.
