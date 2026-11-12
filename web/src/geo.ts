// Distance and city matching for the "recruiting near a city" search and the
// trial finder. Works entirely from the registry's own city coordinates; nothing
// is sent anywhere.

import type { Place, SitesIndex } from "./types";

export type City = Place;

const EARTH_RADIUS_MILES = 3958.8;

export function milesBetween(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_MILES * Math.asin(Math.min(1, Math.sqrt(s)));
}

/** Lower-case, accent-free text for matching ("Zürich" matches "zurich"). */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}

export function cityName(city: City): string {
  return [city.city, city.state, city.country].filter(Boolean).join(", ");
}

/** Cities whose name starts with (or, failing that, contains) what was typed. */
export function matchCities(cities: City[], typed: string, limit = 8): City[] {
  const query = fold(typed);
  if (query.length < 2) return [];
  const starts: City[] = [];
  const contains: City[] = [];
  const seen = new Set<string>();
  for (const city of cities) {
    const name = fold(cityName(city));
    if (seen.has(name)) continue;
    seen.add(name);
    if (name.startsWith(query)) starts.push(city);
    else if (name.includes(query)) contains.push(city);
  }
  const byName = (a: City, b: City) => cityName(a).localeCompare(cityName(b));
  return [...starts.sort(byName), ...contains.sort(byName)].slice(0, limit);
}

export interface NearbyTrial {
  nctId: string;
  nearest: City;
  miles: number;
  citiesInRange: number;
}

/** Trials with a recruiting site within `radius` miles of `origin`, nearest first. */
export function trialsNear(index: SitesIndex, origin: City, radius: number): NearbyTrial[] {
  const distance = index.cities.map((city) => milesBetween(origin, city));
  const found: NearbyTrial[] = [];
  for (const trial of index.trials) {
    let best = -1;
    let inRange = 0;
    for (const i of trial.cities) {
      const miles = distance[i];
      if (miles === undefined || miles > radius) continue;
      inRange += 1;
      if (best === -1 || miles < (distance[best] ?? Infinity)) best = i;
    }
    const nearest = index.cities[best];
    if (nearest) found.push({ nctId: trial.nct_id, nearest, miles: distance[best] ?? 0, citiesInRange: inRange });
  }
  return found.sort((a, b) => a.miles - b.miles || a.nctId.localeCompare(b.nctId));
}
