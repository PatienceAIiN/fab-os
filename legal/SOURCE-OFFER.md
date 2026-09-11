# Written offer for corresponding source

Fab OS images contain software licensed under the GNU GPL, LGPL and other
licenses that require the distributor to make source code available.

**Offer:** Patience AI will provide, to any third party, the complete
corresponding source code for every such package on any Fab OS image we
have distributed, for a period of three years from the release date, for no
more than the cost of physically performing the distribution.

**How to obtain it:**

1. Every image records its exact package list and source-package URIs in
   `legal/source-offer/<image-id>/` of this repository (`manifest.txt`,
   `source-uris.txt`, `copyrights.txt`). These are generated at build time by
   `scripts/source-offer.sh`.
2. For each released image we mirror the referenced `.dsc`, `.orig.tar.*` and
   `.debian.tar.*` files. The mirror location is printed in the release notes
   and in `/usr/share/doc/fabric-branding/SOURCE-OFFER` on the installed
   system.
3. You can also fetch them yourself from the recorded URIs
   (`archive.ubuntu.com` / `snapshot.ubuntu.com`) or run
   `scripts/source-offer.sh --download` against the image.
4. Source for Fab OS's own packages is this repository and the
   `ai-native-os` repository (Apache-2.0).

Requests: SUPPORT_URL in `brand/brand.conf`, or by mail to Patience AI.
