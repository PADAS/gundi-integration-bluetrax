#!/bin/sh

# Creates a PubSub subscription that pushes stage configuration events to an
# arbitrary endpoint — typically an ngrok tunnel in front of a locally running
# action runner, so config changes made in the stage portal reach your laptop.
#
# NAME and INTEGRATION_TYPE are per-developer: pick something unique so your
# subscription does not collide with a teammate's. PUSH_ENDPOINT changes every
# time ngrok restarts, so it is worth passing explicitly:
#
#   NAME=janedoe-bluetrax PUSH_ENDPOINT=https://abcd-1-2-3-4.ngrok-free.app \
#     ./local/helpers/create_stage_config_event_subscription.sh

NAME="${NAME:-yourname-bluetrax}"
PUSH_ENDPOINT="${PUSH_ENDPOINT:-https://your-tunnel.ngrok-free.app}"
INTEGRATION_TYPE="${INTEGRATION_TYPE:-$NAME}"

gcloud pubsub subscriptions create "${NAME}-config-events-sub" \
  --topic=projects/cdip-stage-78ca/topics/configuration-events-stage \
  --push-endpoint="${PUSH_ENDPOINT}" \
  --message-filter="attributes.integration_type = \"${INTEGRATION_TYPE}\""
