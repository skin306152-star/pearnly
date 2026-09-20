"""Keep the existing private control services on local loopback only."""
import os
from target import ssh_command

command = ssh_command("tunnel")
command[1:1] = ["-N", "-T", "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                "-L", "127.0.0.1:18184:127.0.0.1:8184",
                "-L", "127.0.0.1:18180:127.0.0.1:8180"]
os.execvp(command[0], command)
