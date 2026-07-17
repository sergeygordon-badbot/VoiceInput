// Release metadata comes from GitHub so the landing never needs a manual
// version, installer URL, or file-size update.
(() => {
  "use strict";

  const repository = "sergeygordon-badbot/Rechka";
  const apiUrl =
    "https://api.github.com/repos/sergeygordon-badbot/Rechka/releases/latest";
  const releasePage = `https://github.com/${repository}/releases/latest`;
  const cacheKey = "rechka-latest-release-v1";
  const cacheLifetimeMs = 6 * 60 * 60 * 1000;

  function normalizedRelease(payload) {
    const tag = typeof payload?.tag_name === "string" ? payload.tag_name : "";
    const match = /^v?(\d+\.\d+\.\d+)$/.exec(tag.trim());
    if (!match || !Array.isArray(payload?.assets)) {
      return null;
    }

    const version = match[1];
    const expectedName = `Rechka-Setup-${version}.exe`;
    const asset = payload.assets.find((item) => item?.name === expectedName);
    const downloadUrl =
      typeof asset?.browser_download_url === "string"
        ? asset.browser_download_url
        : "";
    const size = Number(asset?.size);
    if (
      !downloadUrl.startsWith(
        `https://github.com/${repository}/releases/download/`
      ) ||
      !Number.isFinite(size) ||
      size <= 0
    ) {
      return null;
    }

    return { version, downloadUrl, size };
  }

  function readCache() {
    try {
      const cached = JSON.parse(sessionStorage.getItem(cacheKey) || "null");
      if (
        cached &&
        Date.now() - Number(cached.savedAt) < cacheLifetimeMs &&
        typeof cached.version === "string" &&
        typeof cached.downloadUrl === "string" &&
        Number(cached.size) > 0
      ) {
        return cached;
      }
    } catch {
      // Private browsing and strict storage policies may disable sessionStorage.
    }
    return null;
  }

  function writeCache(release) {
    try {
      sessionStorage.setItem(
        cacheKey,
        JSON.stringify({ ...release, savedAt: Date.now() })
      );
    } catch {
      // The live GitHub response is still usable when storage is unavailable.
    }
  }

  function applyRelease(release) {
    const sizeRu = `${Math.round(release.size / (1024 * 1024))} МБ`;
    const sizeSchema = `${Math.round(release.size / 1000000)} MB`;

    document.querySelectorAll("[data-release-version]").forEach((element) => {
      element.textContent = release.version;
    });
    document.querySelectorAll("[data-release-size]").forEach((element) => {
      element.textContent = sizeRu;
    });
    document.querySelectorAll("[data-release-download]").forEach((element) => {
      element.href = release.downloadUrl;
    });

    const schemaElement = document.getElementById("software-schema");
    if (schemaElement) {
      try {
        const schema = JSON.parse(schemaElement.textContent);
        schema.softwareVersion = release.version;
        schema.fileSize = sizeSchema;
        schema.downloadUrl = release.downloadUrl;
        schemaElement.textContent = JSON.stringify(schema);
      } catch {
        // Keep the valid static schema when an extension changes its contents.
      }
    }
  }

  const cached = readCache();
  if (cached) {
    applyRelease(cached);
  }

  fetch(apiUrl, {
    headers: { Accept: "application/vnd.github+json" },
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error(`GitHub returned ${response.status}`);
      }
      return response.json();
    })
    .then(normalizedRelease)
    .then((release) => {
      if (!release) {
        throw new Error("The latest release has no trusted installer");
      }
      writeCache(release);
      applyRelease(release);
    })
    .catch(() => {
      document.querySelectorAll("[data-release-download]").forEach((element) => {
        element.href = releasePage;
      });
    });
})();
