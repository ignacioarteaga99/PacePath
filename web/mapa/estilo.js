// Estilo «outdoor» de PacePath para MapLibre (día y noche).
//
// Datos: teselas vectoriales de Protomaps (esquema v4, a partir de
// OpenStreetMap) en un PMTiles propio en Cloudflare R2 (ver
// .github/workflows/mapa.yml), y relieve de los Terrain Tiles de Mapzen en AWS
// Open Data (sombras de cerros y curvas de nivel, calculadas en el teléfono).
// Para correr: parques, bosques y cerros a la vista; senderos, pasajes y
// ciclovías destacados; calles en segundo plano para que la ruta resalte.
(function () {
  const DIA = {
    tierra: "#f3efe4", urbano: "#ede8dc", bosque: "#c9dfb7", pasto: "#dde9c8", matorral: "#dfe5c9",
    arido: "#ece4d3", hielo: "#f5fafc", cultivo: "#ebe8d2", parque: "#d0e8bf", parqueBorde: "#b7d7a3",
    reserva: "#5f8f47", arena: "#f0e6c8", agua: "#a8d1e8", aguaLinea: "#7db4d6", edificio: "#e3ddd1",
    edificioBorde: "#d6cfc1", menor: "#ffffff", menorBorde: "#dcd4c4", mayor: "#fff4dc", mayorBorde: "#e0c48f",
    autopista: "#fde0ae", autopistaBorde: "#d4a254", senda: "#5d8a3a", huella: "#9a7448", cicl: "#3f86d4",
    limite: "#a39a8a", sombra: "#4f4636", luz: "#ffffff", curva: "#a8916a", texto: "#3a3833",
    textoSuave: "#6f6a60", halo: "#ffffff", aguaTexto: "#3f7fae", cerro: "#7a5a33", parqueTexto: "#3f7a34",
  };
  const NOCHE = {
    tierra: "#16181b", urbano: "#1b1d21", bosque: "#1b2a1e", pasto: "#1d2820", matorral: "#1f2720",
    arido: "#24221e", hielo: "#2b3236", cultivo: "#202219", parque: "#1e3322", parqueBorde: "#28452d",
    reserva: "#5aa168", arena: "#2a2720", agua: "#173041", aguaLinea: "#24506d", edificio: "#23252a",
    edificioBorde: "#2c2f35", menor: "#2c2f36", menorBorde: "#16181b", mayor: "#3a3e48", mayorBorde: "#1d1f24",
    autopista: "#4d4636", autopistaBorde: "#1d1f24", senda: "#62b56f", huella: "#b08e63", cicl: "#5b9be3",
    limite: "#5d5a54", sombra: "#000000", luz: "#5a5a5a", curva: "#6b5f4a", texto: "#d4d6dc",
    textoSuave: "#9a9ca4", halo: "#16181b", aguaTexto: "#7fb3d8", cerro: "#c7a57a", parqueTexto: "#7cc483",
  };
  const RELIEVE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
  const nombre = ["coalesce", ["get", "name:es"], ["get", "name"]];
  const ancho = (pares) => ["interpolate", ["exponential", 1.6], ["zoom"], ...pares];
  const es = (prop, valores) => ["in", ["get", prop], ["literal", valores]];

  // Contornos: maplibre-contour genera las curvas de nivel desde el mismo relieve.
  let dem = null;
  function relieve(maplibregl) {
    if (dem || !window.mlcontour) return dem;
    dem = new window.mlcontour.DemSource({ url: RELIEVE, encoding: "terrarium", maxzoom: 12, worker: true, cacheSize: 100, timeoutMs: 10000 });
    dem.setupMaplibre(maplibregl);
    return dem;
  }

  // base: dirección pública de los archivos del mapa en R2 (sin «/» final).
  window.estiloPacePath = function ({ base, noche, maplibregl }) {
    const C = noche ? NOCHE : DIA;
    const d = maplibregl ? relieve(maplibregl) : null;
    const sources = {
      pp: { type: "vector", url: `pmtiles://${base}/chile.pmtiles`,
            attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · <a href="https://protomaps.com">Protomaps</a>' },
      relieve: { type: "raster-dem", tiles: [d ? d.sharedDemProtocolUrl : RELIEVE], encoding: "terrarium", tileSize: 256, maxzoom: 12,
                 attribution: "Relieve: Mapzen, AWS Open Data" },
    };
    if (d) sources.curvas = { type: "vector", maxzoom: 15, tiles: [d.contourProtocolUrl({
      thresholds: { 10: [200, 1000], 12: [100, 500], 13: [50, 250], 14: [20, 100], 15: [10, 50] },
      contourLayer: "curvas", elevationKey: "ele", levelKey: "nivel", extent: 4096, buffer: 1 })] };

    const L = [
      { id: "fondo", type: "background", paint: { "background-color": C.tierra } },
      { id: "tierra", type: "fill", source: "pp", "source-layer": "earth", paint: { "fill-color": C.tierra } },
      // Cobertura del suelo (zoom bajo: cordillera, bosques, desierto)
      { id: "cobertura", type: "fill", source: "pp", "source-layer": "landcover", paint: {
        "fill-color": ["match", ["get", "kind"], "forest", C.bosque, "grassland", C.pasto, "scrub", C.matorral,
          "barren", C.arido, "glacier", C.hielo, "farmland", C.cultivo, "urban_area", C.urbano, C.tierra],
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 7, 1, 10, 0.5] } },
      { id: "uso", type: "fill", source: "pp", "source-layer": "landuse", paint: {
        "fill-color": ["match", ["get", "kind"],
          ["park", "garden", "playground", "dog_park", "golf_course", "grass", "meadow", "village_green", "recreation_ground"], C.parque,
          ["forest", "wood"], C.bosque, ["nature_reserve", "national_park", "protected_area"], C.pasto,
          ["scrub", "heath"], C.matorral, ["beach", "sand"], C.arena, ["glacier"], C.hielo,
          ["farmland", "orchard", "vineyard"], C.cultivo, ["cemetery"], C.pasto, C.urbano],
        "fill-opacity": ["match", ["get", "kind"], ["residential", "commercial", "industrial", "retail", "military", "railway"], 0.35, 1] } },
      { id: "uso-borde", type: "line", source: "pp", "source-layer": "landuse", minzoom: 13,
        filter: es("kind", ["park", "garden", "forest", "wood", "golf_course"]), paint: { "line-color": C.parqueBorde, "line-width": 0.8 } },
      { id: "reserva", type: "line", source: "pp", "source-layer": "landuse",
        filter: es("kind", ["nature_reserve", "national_park", "protected_area"]),
        paint: { "line-color": C.reserva, "line-width": ancho([8, 0.6, 14, 2]), "line-dasharray": [3, 2], "line-opacity": 0.6 } },
      { id: "agua", type: "fill", source: "pp", "source-layer": "water", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": C.agua } },
      { id: "rios", type: "line", source: "pp", "source-layer": "water", filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": C.aguaLinea, "line-width": ["interpolate", ["exponential", 1.6], ["zoom"],
          8, ["match", ["get", "kind"], ["river", "canal"], 0.8, 0], 11, ["match", ["get", "kind"], ["river", "canal"], 1.6, 0.4],
          16, ["match", ["get", "kind"], ["river", "canal"], 6, 2.5]] } },
      // Relieve: sombras de los cerros sobre el suelo, debajo de las calles
      { id: "sombras", type: "hillshade", source: "relieve", paint: {
        "hillshade-shadow-color": C.sombra, "hillshade-highlight-color": C.luz, "hillshade-accent-color": C.sombra,
        "hillshade-exaggeration": ["interpolate", ["linear"], ["zoom"], 6, noche ? 0.55 : 0.45, 14, noche ? 0.3 : 0.22] } },
      ...(d ? [
        { id: "curvas", type: "line", source: "curvas", "source-layer": "curvas", minzoom: 10, paint: {
          "line-color": C.curva, "line-opacity": ["match", ["get", "nivel"], 1, 0.55, 0.3],
          "line-width": ["match", ["get", "nivel"], 1, 1, 0.5] } },
      ] : []),
      { id: "edificios", type: "fill", source: "pp", "source-layer": "buildings", minzoom: 14,
        paint: { "fill-color": C.edificio, "fill-outline-color": C.edificioBorde, "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0, 15, 0.9] } },
      // Calles: bordes y relleno por jerarquía
      { id: "menor-b", type: "line", source: "pp", "source-layer": "roads", minzoom: 13, filter: es("kind", ["minor_road", "other"]),
        layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": C.menorBorde, "line-width": ancho([13, 1.4, 18, 16]) } },
      { id: "mayor-b", type: "line", source: "pp", "source-layer": "roads", minzoom: 10, filter: ["==", ["get", "kind"], "major_road"],
        layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": C.mayorBorde, "line-width": ancho([10, 1.4, 18, 22]) } },
      { id: "autopista-b", type: "line", source: "pp", "source-layer": "roads", minzoom: 7, filter: ["==", ["get", "kind"], "highway"],
        layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": C.autopistaBorde, "line-width": ancho([7, 1.2, 18, 26]) } },
      { id: "menor", type: "line", source: "pp", "source-layer": "roads", minzoom: 12, filter: es("kind", ["minor_road", "other"]),
        layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": C.menor, "line-width": ancho([12, 0.5, 18, 13]) } },
      { id: "mayor", type: "line", source: "pp", "source-layer": "roads", minzoom: 9, filter: ["==", ["get", "kind"], "major_road"],
        layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": C.mayor, "line-width": ancho([9, 0.6, 18, 18]) } },
      { id: "autopista", type: "line", source: "pp", "source-layer": "roads", minzoom: 6, filter: ["==", ["get", "kind"], "highway"],
        layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": C.autopista, "line-width": ancho([6, 0.6, 18, 22]) } },
      // Lo que importa para correr: senderos, huellas y ciclovías
      // Huellas y senderos de cerro: desde lejos y bien marcados.
      { id: "huellas", type: "line", source: "pp", "source-layer": "roads", minzoom: 12,
        filter: ["all", ["==", ["get", "kind"], "path"], es("kind_detail", ["track", "bridleway"])],
        paint: { "line-color": C.huella, "line-width": ancho([12, 0.8, 18, 3.5]), "line-dasharray": [2.5, 1.5] } },
      { id: "senderos", type: "line", source: "pp", "source-layer": "roads", minzoom: 12,
        filter: ["all", ["==", ["get", "kind"], "path"], ["==", ["get", "kind_detail"], "path"]],
        paint: { "line-color": C.senda, "line-width": ancho([12, 0.8, 18, 3.5]), "line-dasharray": [1.5, 1] } },
      // Veredas, cruces y pasajes peatonales: en Santiago están casi todas
      // mapeadas aparte de la calle; sólo de muy cerca y suaves (si no, el
      // mapa queda lleno de líneas verdes).
      { id: "peatonales", type: "line", source: "pp", "source-layer": "roads", minzoom: 16,
        filter: ["all", ["==", ["get", "kind"], "path"],
                 ["!", es("kind_detail", ["path", "track", "bridleway", "cycleway", "sidewalk", "crossing"])],
                 ["!", es("footway", ["sidewalk", "crossing"])]],
        paint: { "line-color": C.senda, "line-opacity": 0.45, "line-width": ancho([16, 0.6, 18, 1.6]), "line-dasharray": [1, 1.5] } },
      { id: "ciclovias", type: "line", source: "pp", "source-layer": "roads", minzoom: 13, filter: ["==", ["get", "kind_detail"], "cycleway"],
        paint: { "line-color": C.cicl, "line-width": ancho([13, 0.8, 18, 3]), "line-dasharray": [2, 1.2] } },
      { id: "limites", type: "line", source: "pp", "source-layer": "boundaries", filter: es("kind", ["country", "region"]),
        paint: { "line-color": C.limite, "line-width": ancho([3, 0.6, 10, 1.6]), "line-dasharray": [4, 2] } },
      // Puntos útiles para correr
      { id: "agua-potable", type: "circle", source: "pp", "source-layer": "pois", minzoom: 14, filter: ["==", ["get", "kind"], "drinking_water"],
        paint: { "circle-radius": 4.5, "circle-color": "#2a8bd6", "circle-stroke-color": "#fff", "circle-stroke-width": 1.6 } },
      { id: "banos", type: "circle", source: "pp", "source-layer": "pois", minzoom: 15, filter: ["==", ["get", "kind"], "toilets"],
        paint: { "circle-radius": 3.5, "circle-color": "#8a8f99", "circle-stroke-color": "#fff", "circle-stroke-width": 1.4 } },
      // Nombres
      { id: "rios-nombre", type: "symbol", source: "pp", "source-layer": "water", minzoom: 12, filter: ["==", ["geometry-type"], "LineString"],
        layout: { "symbol-placement": "line", "text-field": nombre, "text-font": ["Noto Sans Italic"], "text-size": 12, "text-letter-spacing": 0.08 },
        paint: { "text-color": C.aguaTexto, "text-halo-color": C.halo, "text-halo-width": 1.4 } },
      ...(d ? [{ id: "curvas-cota", type: "symbol", source: "curvas", "source-layer": "curvas", minzoom: 12, filter: [">", ["get", "nivel"], 0],
        layout: { "symbol-placement": "line", "text-field": ["concat", ["to-string", ["get", "ele"]], " m"], "text-font": ["Noto Sans Regular"], "text-size": 10 },
        paint: { "text-color": C.curva, "text-halo-color": C.halo, "text-halo-width": 1.2 } }] : []),
      { id: "calles-nombre", type: "symbol", source: "pp", "source-layer": "roads", minzoom: 14,
        filter: es("kind", ["major_road", "minor_road", "highway"]),
        layout: { "symbol-placement": "line", "text-field": nombre, "text-font": ["Noto Sans Regular"], "text-size": ancho([14, 10.5, 18, 14]),
                  "symbol-spacing": 350 },
        paint: { "text-color": C.textoSuave, "text-halo-color": C.halo, "text-halo-width": 1.6 } },
      { id: "senderos-nombre", type: "symbol", source: "pp", "source-layer": "roads", minzoom: 15, filter: ["==", ["get", "kind"], "path"],
        layout: { "symbol-placement": "line", "text-field": nombre, "text-font": ["Noto Sans Italic"], "text-size": 11 },
        paint: { "text-color": C.parqueTexto, "text-halo-color": C.halo, "text-halo-width": 1.4 } },
      { id: "parques-nombre", type: "symbol", source: "pp", "source-layer": "pois",
        filter: ["all", es("kind", ["park", "nature_reserve", "national_park", "protected_area", "garden", "forest", "wood"]),
                 [">=", ["zoom"], ["coalesce", ["get", "min_zoom"], 14]]],
        layout: { "text-field": nombre, "text-font": ["Noto Sans Italic"], "text-size": 12, "text-max-width": 8 },
        paint: { "text-color": C.parqueTexto, "text-halo-color": C.halo, "text-halo-width": 1.5 } },
      { id: "cerros", type: "symbol", source: "pp", "source-layer": "pois",
        filter: ["all", ["in", ["get", "kind"], ["literal", ["peak", "volcano"]]], [">=", ["zoom"], ["coalesce", ["get", "min_zoom"], 11]]],
        layout: { "text-field": ["format", "▲ ", {}, nombre, {}, ["case", ["has", "elevation"], ["concat", "\n", ["to-string", ["round", ["get", "elevation"]]], " m"], ""], { "font-scale": 0.85 }],
                  "text-font": ["Noto Sans Medium"], "text-size": 11.5, "text-max-width": 8 },
        paint: { "text-color": C.cerro, "text-halo-color": C.halo, "text-halo-width": 1.5 } },
      { id: "lugares", type: "symbol", source: "pp", "source-layer": "places",
        filter: [">=", ["zoom"], ["coalesce", ["get", "min_zoom"], 0]],
        layout: { "text-field": nombre, "text-font": ["Noto Sans Medium"], "text-max-width": 8,
                  "text-size": ["interpolate", ["linear"], ["zoom"],
                    6, ["match", ["get", "kind"], "country", 14, "region", 13, "locality", 11, 11],
                    12, ["match", ["get", "kind"], "country", 14, "region", 13, "locality", 15, 12.5]],
                  "text-transform": ["match", ["get", "kind"], ["country", "region"], "uppercase", "none"],
                  "text-letter-spacing": ["match", ["get", "kind"], ["country", "region"], 0.12, 0.02] },
        paint: { "text-color": ["match", ["get", "kind"], ["neighbourhood", "macrohood", "microhood"], C.textoSuave, C.texto],
                 "text-halo-color": C.halo, "text-halo-width": 1.8 } },
    ];
    return { version: 8, glyphs: `${base}/fonts/{fontstack}/{range}.pbf`, sources, layers: L };
  };
})();
