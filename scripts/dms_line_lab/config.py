"""Only localhost laboratory identities; never import production LINE credentials."""
import hashlib

SITES = ("dms-line-lab.localhost", "dms-line-lab-b.localhost")


def channel_config(site):
    if site not in SITES:
        raise ValueError("Unknown laboratory site")
    number = str(1111111111 + SITES.index(site))
    return {
        "pearnly_product": "dms",
        "pearnly_line_channel_id": "2" + number[1:],
        "pearnly_line_liff_id": number + "-lab",
        "pearnly_line_channel_secret": hashlib.sha256(("lab-only:" + site).encode()).hexdigest(),
        "pearnly_line_channel_access_token": "lab:" + site,
    }
