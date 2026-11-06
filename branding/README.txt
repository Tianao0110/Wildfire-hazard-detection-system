BRANDING FOLDER
===============

Put your brand assets in this folder and restart the app — no code changes.

1) LOGO
   Add a file named logo.png or logo.jpg (see LOGO_GUIDE.txt for format/size).
   It appears in the console UI and the PDF reports automatically.
   With no logo file, only the text title is shown.

2) NAME / SUBTITLE / COLORS
   Edit brand.json:
   - "app_name"             : main title (e.g. "CIRUS Wildfire Detection")
   - "subtitle"             : subtitle (e.g. "Operations Console")
   - "colors.primary"       : theme primary color (dark; nav highlight/buttons),
                              hex, e.g. "#1B4079"
   - "colors.primary_light" : theme light color (status dots/progress), hex

3) TO APPLY
   Restart the desktop app (close the window and reopen). The UI reads the
   latest content of this folder at startup.

NOTE: logo.png and brand.json ARE tracked in Git - they are the project's
own neutral mark (two conifers on a pine-green tile) and its colours. To brand
a deployment for a client, replace them locally, then rebuild the desktop icon
with `python -m scripts.install_desktop_app`; the icon is cut from logo.png.

Do not put an institution's or client's logo here without written permission.
A logo is a trademark: using one as a product's identity says that
organisation made or endorses the product.
