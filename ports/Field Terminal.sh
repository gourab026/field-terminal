#!/bin/bash
# Field Terminal - system monitor and network lab for Knulli handhelds (Python + pygame), launched from Ports.
# Ports run as root on Knulli, which raw sockets (packet capture, ARP sweep, traceroute) need.

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}

if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi

source "$controlfolder/control.txt"
[ -f "${controlfolder}/mod_${CFW_NAME}.txt" ] && source "${controlfolder}/mod_${CFW_NAME}.txt"
get_controls

GAMEDIR="/$directory/ports/fieldterminal"
cd "$GAMEDIR" || exit 1

export SDL_GAMECONTROLLERCONFIG="$sdl_controllerconfig"
export PYTHONDONTWRITEBYTECODE=1

python3 main.py > "$GAMEDIR/log.txt" 2>&1

pm_finish
