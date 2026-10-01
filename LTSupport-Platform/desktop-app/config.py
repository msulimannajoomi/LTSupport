# Point these at your deployed server's public IP/domain before distributing to clients.
# HTTPS now, through the same Caddy reverse proxy already fronting the static pages
# below -- real TLS for login/billing/AI Assistant traffic, with zero server-side
# risk since Caddy's already been reliably proxying this exact path all along.
API_BASE_URL = "https://testingpaddletl.najoomi.ai"
# Still plaintext, deliberately -- the relay (unlike the API above) isn't behind
# Caddy, and flipping it to TLS-only breaks every already-installed client's
# remote sessions the instant the server switches over, with no way to warn them
# first. Flip RELAY_HOST to a real hostname (a bare IP never matches a TLS cert)
# and RELAY_USE_TLS to True together, only once that's coordinated.
RELAY_HOST = "136.116.38.224"
RELAY_PORT = 7000
RELAY_USE_TLS = False

# The public, Caddy-fronted HTTPS domain -- NOT the same as API_BASE_URL above, which
# points directly at the backend's own port and has no static file serving of its
# own. Used for static pages opened in the system browser (see billing_view.py's
# interim request-upgrade.html link, and the Stripe checkout success/cancel pages
# the backend itself points Stripe at).
PUBLIC_WEB_BASE_URL = "https://testingpaddletl.najoomi.ai"

# Host system audio -> viewer speaker. Raw PCM16 mono, no compression -- both sides
# must agree on this format since audio frames carry no header of their own.
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCK_SIZE = 1024  # samples per chunk sent (~64ms at 16kHz)

# Where session recordings are saved on the viewer's machine.
RECORDINGS_DIR_NAME = "Recordings"
