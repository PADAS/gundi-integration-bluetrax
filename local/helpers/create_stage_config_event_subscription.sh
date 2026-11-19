#!/bin/sh

# Creates a PubSub subscription that pushes stage configuration events to a
# locally running action runner through a tunnel (typically ngrok), so config
# changes made in the stage portal reach your laptop.
#
# NAME and INTEGRATION_TYPE are per-developer: pick something unique so your
# subscription does not collide with a teammate's. TUNNEL_URL is the tunnel's
# base URL; the runner's configuration-event route (/config-events/) is
# appended here. It changes every time ngrok restarts, so pass it explicitly:
#
#   NAME=janedoe-bluetrax TUNNEL_URL=https://abcd-1-2-3-4.ngrok-free.app \
#     ./local/helpers/create_stage_config_event_subscription.sh

STAGE_PROJECT="cdip-stage-78ca"

NAME="${NAME:-yourname-bluetrax}"
TUNNEL_URL="${TUNNEL_URL:-https://your-tunnel.ngrok-free.app}"
INTEGRATION_TYPE="${INTEGRATION_TYPE:-$NAME}"

# The subscription is created in the stage project whatever gcloud's active
# project is: --topic's project applies to the topic only.
gcloud pubsub subscriptions create "${NAME}-config-events-sub" \
  --project="${STAGE_PROJECT}" \
  --topic="projects/${STAGE_PROJECT}/topics/configuration-events-stage" \
  --push-endpoint="${TUNNEL_URL%/}/config-events/" \
  --message-filter="attributes.integration_type = \"${INTEGRATION_TYPE}\""
