# ▲ Zelda OoT Remake LAN Co-oP ▲

**Two-player co-op for *The Legend of Zelda: Ocarina of Time* (Nintendo Switch 2 edition), running in the Lichii emulator on two PCs on the same network.**

I made this so I could play Ocarina of Time together with my daughter – both of us in the same Hyrule at the same time, each on our own PC.

> ⚠️ **Work in progress.** We have not finished the game yet, so there are surely still plenty of bugs. They get fewer the closer we get to Ganon. 🙂

---

## What it does

Each player runs the game on their own PC. The mod connects the two games over the local network and keeps them in step:

- **Your friend appears in your game as "Link 2"** – a real second Link that walks, runs, jumps, fights and opens chests, steered by your friend's controller. Link 2 appears when your friend connects and disappears when they leave.
- **Zones:** with *FOLLOW* turned on, you are taken along when your friend goes through a door or into a new area. Start points are learned automatically as you play.
- **Time of day and weather** follow the host PC.
- **World progress** (opened chests, bombed walls, switches, collected keys …) is shared, so a chest opened by one player is open for both.
- **Enemies:** an enemy killed by one player is gone for both, and normal enemies are shared between the two games.
- **Hearts and ammo** behave sensibly: picking up a heart heals you, and nuts/seeds are only used by the player who throws them.
- **Look:** choose *Normal* or *No tunic* so you can tell the two Links apart.
- A small Zelda-styled window shows status, ping, packets, zone and a chat.

## What you need

- A Windows PC per player, both on the same local network.
- **Your own legal copy** of Ocarina of Time for Switch 2 – this repository contains **no** game files, keys or ROMs.
- The Lichii emulator (tested with build-160).
- Python 3.12 if you want to build the mod yourself.

## Install

1. Copy the patch files from [`patches/`](patches/) into Lichii's mod folder on **both** PCs:
   In Lichii: right-click the game → **Open Mod Data Location**, and put them in a new folder `OoT_MP_Test/exefs/` there.
2. Start the game in Lichii and load your save on both PCs.
3. Run the mod on both PCs (see *Build*), type a name, and press **Connect** on one of them (or pick your friend from the list).

UDP ports **27015** (game data) and **27016** (finding each other on the network) must be open between the two PCs.

## Build it yourself

No `.exe` is included – build it from source:

```bash
pip install -r requirements.txt
cd src
python -m PyInstaller --noconfirm OoT_Multiplayer.spec
```

The finished program is `src/dist/OoT_Multiplayer.exe`. You can also just run `python src/oot_mp_gui.py`.

## Known limitations

- Bosses are not synced yet (each player fights their own boss; the victory is shared through world flags).
- Damage dealt to an enemy steered by the other PC is not shared yet.
- Night skeletons are separate on each PC.
- Only works with the game version we play on; a game update breaks the mod. Emulator updates usually work, but test on one PC first.

## License

Copyright (C) 2026 NorPat. This project is licensed under the **GNU General Public License v3.0** – see [LICENSE](LICENSE). You may use, study, share and modify it under the terms of the GPLv3; derived works must also be released under the GPLv3.

## Credits

Made by **NorPat** – for father–daughter co-op in Hyrule.

*The Legend of Zelda* is a trademark of Nintendo. This is an unofficial fan project and is not affiliated with or endorsed by Nintendo.
