"""Hand-authored synthetic world for QLOO_MOCK=1.

NOTHING HERE IS QLOO DATA. City coordinates are public geography; every
affinity, popularity, venue and local act below is invented so the app runs
end-to-end before a hackathon API key arrives. Venue and opening-act names are
fictional on purpose so no one mistakes a mock result for a real booking lead.

Taste is modelled as a small vector over genre axes; mock affinity is cosine
similarity between an artist's vector and a city's, venue's or act's vector.
That keeps mock answers coherent (a folk artist over-indexes in folk towns)
without pretending to be Qloo's model.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass

AXES = ("folk", "indie", "dreampop", "psychsoul", "punk", "electronic", "hiphop", "country", "jazz")
_NS = uuid.UUID("6f1d7a52-0000-4000-8000-00000000a0ad")


def mock_id(kind: str, name: str) -> str:
    return str(uuid.uuid5(_NS, f"{kind}:{name}")).upper()


def vec(**weights: float) -> tuple[float, ...]:
    return tuple(float(weights.get(axis, 0.0)) for axis in AXES)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def hashed_vector(name: str) -> tuple[float, ...]:
    """Stable pseudo-taste for artists the fixture does not know."""
    digest = hashlib.sha256(name.lower().encode()).digest()
    raw = [digest[i] / 255 for i in range(len(AXES))]
    top = sorted(range(len(AXES)), key=lambda i: raw[i], reverse=True)[:3]
    return tuple(raw[i] if i in top else raw[i] * 0.15 for i in range(len(AXES)))


@dataclass(frozen=True)
class MockCity:
    name: str
    lat: float
    lon: float
    country_code: str
    admin1: str
    taste: tuple[float, ...]
    popularity: float

    @property
    def entity_id(self) -> str:
        return mock_id("destination", self.name)


@dataclass(frozen=True)
class MockArtist:
    name: str
    taste: tuple[float, ...]
    popularity: float
    home_city: str = ""
    disambiguation: str = ""

    @property
    def entity_id(self) -> str:
        return mock_id("artist", self.name)


@dataclass(frozen=True)
class MockVenue:
    name: str
    city: str
    lat: float
    lon: float
    taste: tuple[float, ...]
    popularity: float
    address: str

    @property
    def entity_id(self) -> str:
        return mock_id("place", f"{self.name}|{self.city}")


C = MockCity
CITIES: tuple[MockCity, ...] = (
    # Pacific Northwest
    C("Seattle", 47.6062, -122.3321, "US", "Washington", vec(indie=.9, punk=.6, folk=.6, hiphop=.4, electronic=.4), .95),
    C("Portland", 45.5152, -122.6784, "US", "Oregon", vec(folk=.9, indie=.9, dreampop=.6, punk=.5, psychsoul=.4), .9),
    C("Olympia", 47.0379, -122.9007, "US", "Washington", vec(punk=.9, indie=.8, folk=.5), .45),
    C("Eugene", 44.0521, -123.0868, "US", "Oregon", vec(folk=.8, psychsoul=.7, indie=.5, jazz=.3), .5),
    C("Bellingham", 48.7519, -122.4787, "US", "Washington", vec(folk=.8, indie=.7, dreampop=.4), .4),
    C("Spokane", 47.6588, -117.4260, "US", "Washington", vec(country=.6, indie=.5, punk=.5, hiphop=.3), .55),
    C("Boise", 43.6150, -116.2023, "US", "Idaho", vec(indie=.7, country=.5, folk=.5, punk=.4), .55),
    C("Vancouver", 49.2827, -123.1207, "CA", "British Columbia", vec(indie=.8, electronic=.7, dreampop=.6, folk=.4), .9),
    C("Tacoma", 47.2529, -122.4443, "US", "Washington", vec(punk=.7, hiphop=.5, indie=.6), .5),
    # California
    C("Los Angeles", 34.0522, -118.2437, "US", "California", vec(indie=.8, hiphop=.8, electronic=.7, dreampop=.6, psychsoul=.6), 1.0),
    C("San Francisco", 37.7749, -122.4194, "US", "California", vec(indie=.7, electronic=.7, psychsoul=.6, jazz=.5), .95),
    C("Oakland", 37.8044, -122.2712, "US", "California", vec(hiphop=.8, psychsoul=.8, punk=.6, jazz=.5), .75),
    C("San Diego", 32.7157, -117.1611, "US", "California", vec(indie=.6, punk=.7, psychsoul=.5, dreampop=.4), .85),
    C("Sacramento", 38.5816, -121.4944, "US", "California", vec(indie=.6, punk=.6, hiphop=.5, country=.4), .7),
    C("Santa Cruz", 36.9741, -122.0308, "US", "California", vec(folk=.8, psychsoul=.7, dreampop=.6), .45),
    C("Fresno", 36.7378, -119.7871, "US", "California", vec(country=.6, hiphop=.6, punk=.4), .6),
    C("Santa Barbara", 34.4208, -119.6982, "US", "California", vec(folk=.7, dreampop=.6, indie=.6), .5),
    # Texas / Southwest
    C("Austin", 30.2672, -97.7431, "US", "Texas", vec(indie=.9, psychsoul=.8, country=.7, folk=.6, punk=.5), .95),
    C("Houston", 29.7604, -95.3698, "US", "Texas", vec(hiphop=.9, psychsoul=.7, country=.5, electronic=.4), .95),
    C("Dallas", 32.7767, -96.7970, "US", "Texas", vec(hiphop=.7, country=.7, indie=.5), .9),
    C("San Antonio", 29.4241, -98.4936, "US", "Texas", vec(country=.7, psychsoul=.5, hiphop=.5), .8),
    C("Denton", 33.2148, -97.1331, "US", "Texas", vec(indie=.8, jazz=.7, punk=.6, folk=.5), .4),
    C("Marfa", 30.3094, -104.0206, "US", "Texas", vec(folk=.7, psychsoul=.7, dreampop=.5), .2),
    C("Phoenix", 33.4484, -112.0740, "US", "Arizona", vec(punk=.6, hiphop=.6, indie=.5, country=.4), .85),
    C("Tucson", 32.2226, -110.9747, "US", "Arizona", vec(folk=.7, psychsoul=.7, indie=.6, country=.4), .6),
    C("Albuquerque", 35.0844, -106.6504, "US", "New Mexico", vec(folk=.6, psychsoul=.6, punk=.5), .6),
    C("Las Vegas", 36.1699, -115.1398, "US", "Nevada", vec(electronic=.9, hiphop=.6, punk=.4), .85),
    # Midwest
    C("Chicago", 41.8781, -87.6298, "US", "Illinois", vec(indie=.8, hiphop=.8, jazz=.8, electronic=.6, punk=.6), 1.0),
    C("Milwaukee", 43.0389, -87.9065, "US", "Wisconsin", vec(indie=.7, punk=.6, folk=.5), .65),
    C("Madison", 43.0731, -89.4012, "US", "Wisconsin", vec(folk=.8, indie=.8, dreampop=.5), .55),
    C("Minneapolis", 44.9778, -93.2650, "US", "Minnesota", vec(indie=.9, folk=.7, hiphop=.6, punk=.5), .85),
    C("Detroit", 42.3314, -83.0458, "US", "Michigan", vec(electronic=.9, psychsoul=.8, hiphop=.7, punk=.5), .85),
    C("Ann Arbor", 42.2808, -83.7430, "US", "Michigan", vec(folk=.8, indie=.7, jazz=.5), .45),
    C("St. Louis", 38.6270, -90.1994, "US", "Missouri", vec(hiphop=.6, jazz=.6, country=.5, indie=.5), .75),
    C("Indianapolis", 39.7684, -86.1581, "US", "Indiana", vec(indie=.5, country=.6, hiphop=.5), .7),
    C("Columbus", 39.9612, -82.9988, "US", "Ohio", vec(indie=.7, punk=.6, hiphop=.5), .75),
    C("Bloomington", 39.1653, -86.5264, "US", "Indiana", vec(folk=.8, indie=.8, jazz=.4), .35),
    # Northeast
    C("New York", 40.7128, -74.0060, "US", "New York", vec(indie=.9, hiphop=.9, jazz=.8, electronic=.8, punk=.7, dreampop=.6), 1.0),
    C("Brooklyn", 40.6782, -73.9442, "US", "New York", vec(indie=.95, dreampop=.8, psychsoul=.7, punk=.6, folk=.5), .9),
    C("Boston", 42.3601, -71.0589, "US", "Massachusetts", vec(indie=.7, folk=.7, punk=.6, jazz=.5), .9),
    C("Philadelphia", 39.9526, -75.1652, "US", "Pennsylvania", vec(indie=.8, hiphop=.7, punk=.7, psychsoul=.6), .9),
    C("Providence", 41.8240, -71.4128, "US", "Rhode Island", vec(punk=.8, indie=.7, folk=.5), .55),
    C("Burlington", 44.4759, -73.2121, "US", "Vermont", vec(folk=.9, indie=.6, jazz=.4), .4),
    C("Northampton", 42.3251, -72.6412, "US", "Massachusetts", vec(folk=.8, indie=.8, dreampop=.5), .35),
    C("Washington", 38.9072, -77.0369, "US", "District of Columbia", vec(hiphop=.7, punk=.7, jazz=.6, indie=.6), .9),
    C("Baltimore", 39.2904, -76.6122, "US", "Maryland", vec(electronic=.6, punk=.6, indie=.6, hiphop=.6), .75),
    C("Hudson", 42.2529, -73.7910, "US", "New York", vec(folk=.8, indie=.6, dreampop=.5, jazz=.4), .25),
    # Southeast
    C("Nashville", 36.1627, -86.7816, "US", "Tennessee", vec(country=.95, folk=.7, indie=.6, psychsoul=.4), .95),
    C("Atlanta", 33.7490, -84.3880, "US", "Georgia", vec(hiphop=.95, psychsoul=.7, indie=.5, electronic=.5), .95),
    C("Asheville", 35.5951, -82.5515, "US", "North Carolina", vec(folk=.95, psychsoul=.6, indie=.6, country=.5), .55),
    C("Athens", 33.9519, -83.3576, "US", "Georgia", vec(indie=.9, folk=.6, punk=.6, dreampop=.5), .45),
    C("Chapel Hill", 35.9132, -79.0558, "US", "North Carolina", vec(indie=.8, folk=.7, punk=.5), .45),
    C("Durham", 35.9940, -78.8986, "US", "North Carolina", vec(indie=.7, folk=.6, hiphop=.5, jazz=.5), .55),
    C("Knoxville", 35.9606, -83.9207, "US", "Tennessee", vec(country=.7, folk=.6, indie=.5), .5),
    C("Birmingham", 33.5186, -86.8104, "US", "Alabama", vec(country=.6, psychsoul=.6, hiphop=.5), .6),
    C("New Orleans", 29.9511, -90.0715, "US", "Louisiana", vec(jazz=.95, psychsoul=.9, hiphop=.6, folk=.4), .9),
    C("Savannah", 32.0809, -81.0912, "US", "Georgia", vec(folk=.6, indie=.5, psychsoul=.5), .5),
    C("Memphis", 35.1495, -90.0490, "US", "Tennessee", vec(psychsoul=.8, hiphop=.8, country=.5), .75),
    # Mountain West
    C("Denver", 39.7392, -104.9903, "US", "Colorado", vec(indie=.8, electronic=.7, folk=.6, psychsoul=.5), .9),
    C("Boulder", 40.0150, -105.2705, "US", "Colorado", vec(folk=.9, psychsoul=.6, indie=.6, electronic=.4), .5),
    C("Salt Lake City", 40.7608, -111.8910, "US", "Utah", vec(indie=.7, punk=.5, dreampop=.5, folk=.5), .75),
    C("Fort Collins", 40.5853, -105.0844, "US", "Colorado", vec(folk=.7, indie=.6, country=.4), .45),
    C("Missoula", 46.8721, -113.9940, "US", "Montana", vec(folk=.8, country=.6, indie=.5), .35),
    C("Santa Fe", 35.6870, -105.9378, "US", "New Mexico", vec(folk=.7, psychsoul=.6, jazz=.5), .45),
    # United Kingdom
    C("London", 51.5074, -0.1278, "GB", "England", vec(indie=.9, electronic=.9, hiphop=.8, punk=.7, jazz=.7, dreampop=.6), 1.0),
    C("Manchester", 53.4808, -2.2426, "GB", "England", vec(indie=.95, electronic=.7, punk=.6, dreampop=.5), .9),
    C("Glasgow", 55.8642, -4.2518, "GB", "Scotland", vec(indie=.9, folk=.6, punk=.6, dreampop=.6), .85),
    C("Bristol", 51.4545, -2.5879, "GB", "England", vec(electronic=.9, psychsoul=.6, indie=.6, hiphop=.5), .8),
    C("Leeds", 53.8008, -1.5491, "GB", "England", vec(indie=.8, punk=.7, electronic=.5), .75),
    C("Brighton", 50.8225, -0.1372, "GB", "England", vec(indie=.8, folk=.6, dreampop=.6, punk=.5), .7),
    C("Edinburgh", 55.9533, -3.1883, "GB", "Scotland", vec(folk=.8, indie=.7, jazz=.4), .8),
    C("Liverpool", 53.4084, -2.9916, "GB", "England", vec(indie=.8, psychsoul=.6, folk=.5), .8),
    C("Cardiff", 51.4816, -3.1791, "GB", "Wales", vec(indie=.7, punk=.6, folk=.5), .7),
    # Luzon, Philippines
    C("Manila", 14.5995, 120.9842, "PH", "Metro Manila", vec(indie=.7, hiphop=.7, folk=.6, dreampop=.5, electronic=.5), 1.0),
    C("Quezon City", 14.6760, 121.0437, "PH", "Metro Manila", vec(indie=.85, punk=.6, folk=.6, psychsoul=.5), .9),
    C("Makati", 14.5547, 121.0244, "PH", "Metro Manila", vec(electronic=.8, jazz=.6, hiphop=.6, indie=.5), .85),
    C("Baguio", 16.4023, 120.5960, "PH", "Benguet", vec(folk=.95, indie=.7, dreampop=.5, jazz=.4), .6),
    C("Angeles", 15.1450, 120.5887, "PH", "Pampanga", vec(electronic=.6, hiphop=.6, indie=.5), .55),
    C("Naga", 13.6218, 123.1948, "PH", "Camarines Sur", vec(folk=.7, indie=.6, punk=.5), .45),
    C("Legazpi", 13.1391, 123.7438, "PH", "Albay", vec(folk=.7, indie=.5, hiphop=.4), .4),
    C("Lucena", 13.9414, 121.6234, "PH", "Quezon", vec(folk=.6, hiphop=.5, punk=.4), .35),
    C("San Fernando", 16.6159, 120.3166, "PH", "La Union", vec(folk=.8, dreampop=.7, psychsoul=.6, indie=.6), .45),
    C("Tagaytay", 14.1153, 120.9621, "PH", "Cavite", vec(folk=.7, jazz=.5, dreampop=.4), .4),
    # East Coast Australia
    C("Sydney", -33.8688, 151.2093, "AU", "New South Wales", vec(indie=.8, electronic=.8, hiphop=.6, psychsoul=.5), 1.0),
    C("Melbourne", -37.8136, 144.9631, "AU", "Victoria", vec(indie=.95, psychsoul=.8, punk=.7, jazz=.6, folk=.6), 1.0),
    C("Brisbane", -27.4698, 153.0251, "AU", "Queensland", vec(indie=.7, punk=.6, electronic=.5), .85),
    C("Byron Bay", -28.6474, 153.6020, "AU", "New South Wales", vec(folk=.8, psychsoul=.8, dreampop=.6), .4),
    C("Newcastle", -32.9283, 151.7817, "AU", "New South Wales", vec(punk=.7, indie=.6, folk=.4), .55),
    C("Canberra", -35.2809, 149.1300, "AU", "Australian Capital Territory", vec(indie=.6, folk=.6, jazz=.4), .6),
    C("Gold Coast", -28.0167, 153.4000, "AU", "Queensland", vec(electronic=.7, indie=.5, punk=.4), .7),
)

A = MockArtist
ARTISTS: tuple[MockArtist, ...] = (
    A("Phoebe Bridgers", vec(folk=.85, indie=.8, dreampop=.5), .92, disambiguation="Singer-songwriter"),
    A("Big Thief", vec(folk=.9, indie=.75, dreampop=.3), .82, disambiguation="Indie folk band"),
    A("Bon Iver", vec(folk=.9, indie=.6, electronic=.3, dreampop=.4), .9, disambiguation="Indie folk project"),
    A("Japanese Breakfast", vec(indie=.85, dreampop=.75, electronic=.3), .8, disambiguation="Indie pop project"),
    A("Khruangbin", vec(psychsoul=.95, jazz=.4, indie=.4, dreampop=.3), .88, disambiguation="Psychedelic trio"),
    A("Turnstile", vec(punk=.95, indie=.35, hiphop=.1), .8, disambiguation="Hardcore band"),
    A("Mac DeMarco", vec(indie=.85, dreampop=.6, psychsoul=.4), .85, disambiguation="Singer-songwriter"),
    A("Beach House", vec(dreampop=.95, indie=.6, electronic=.3), .84, disambiguation="Dream pop duo"),
    A("Waxahatchee", vec(folk=.8, country=.6, indie=.6), .7, disambiguation="Singer-songwriter"),
    A("Thundercat", vec(jazz=.8, psychsoul=.8, electronic=.4, hiphop=.4), .8, disambiguation="Bassist"),
    A("Up Dharma Down", vec(folk=.85, indie=.6, dreampop=.3), .78, disambiguation="Filipino folk-pop band"),
    A("IV of Spades", vec(psychsoul=.8, indie=.7, jazz=.3), .7, disambiguation="Filipino funk-rock band"),
    A("Unique Salonga", vec(indie=.8, psychsoul=.5, folk=.5), .6, disambiguation="Filipino singer-songwriter"),
)

# Fictional local acts, one trio of names per city, generated from word lists.
_ADJ = ("Pale", "Velvet", "Copper", "Hollow", "Quiet", "Neon", "Paper", "Wild", "Lantern",
        "Silver", "Low", "Salt", "Amber", "Northern", "Glass", "Cedar")
_NOUN = ("Ferns", "Harbors", "Moths", "Satellites", "Orchards", "Tides", "Cassettes", "Foxes",
         "Ghosts", "Rivers", "Signals", "Pines", "Arcades", "Comets", "Kites", "Mirrors")
_VENUE_FORMS = ("The {} Room", "{} Hall", "The {} Tavern", "{} Social Club", "The {} Lounge")
_VENUE_WORDS = ("Lantern", "Foundry", "Boxcar", "Wren", "Atlas", "Sparrow", "Marquee", "Ember",
                "Cobalt", "Juniper", "Orbit", "Willow", "Anchor", "Rook", "Harbor", "Mercury")


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def _jitter(base: tuple[float, ...], seed: int, amount: float = 0.35) -> tuple[float, ...]:
    out = []
    for i, value in enumerate(base):
        wobble = ((seed >> (i * 5)) & 31) / 31 - 0.5
        out.append(max(0.0, min(1.0, value + wobble * amount)))
    return tuple(out)


def local_acts(city: MockCity) -> list[MockArtist]:
    acts = []
    for n in range(3):
        s = _seed(f"act:{city.name}:{n}")
        name = f"{_ADJ[s % len(_ADJ)]} {_NOUN[(s // 7) % len(_NOUN)]}"
        acts.append(
            MockArtist(
                name=f"{name} ({city.name})",
                taste=_jitter(city.taste, s),
                popularity=round(0.08 + (s % 30) / 100, 3),
                home_city=city.name,
                disambiguation=f"Fictional mock act from {city.name}",
            )
        )
    return acts


def venues(city: MockCity) -> list[MockVenue]:
    out = []
    for n in range(4):
        s = _seed(f"venue:{city.name}:{n}")
        form = _VENUE_FORMS[s % len(_VENUE_FORMS)]
        word = _VENUE_WORDS[(s // 11) % len(_VENUE_WORDS)]
        dlat = (((s >> 8) % 100) / 100 - 0.5) * 0.04
        dlon = (((s >> 16) % 100) / 100 - 0.5) * 0.04
        out.append(
            MockVenue(
                name=form.format(word),
                city=city.name,
                lat=round(city.lat + dlat, 5),
                lon=round(city.lon + dlon, 5),
                taste=_jitter(city.taste, s, 0.5),
                popularity=round(0.25 + 0.7 * ((s >> 24) % 100) / 100, 3),
                address=f"{100 + s % 900} {word} St, {city.name}",
            )
        )
    return out


ALL_LOCAL_ACTS: tuple[MockArtist, ...] = tuple(a for c in CITIES for a in local_acts(c))
ALL_VENUES: tuple[MockVenue, ...] = tuple(v for c in CITIES for v in venues(c))
CITY_BY_NAME = {c.name: c for c in CITIES}

VENUE_TAG_ID = "urn:tag:category:place:music_venue"
MOCK_TAGS: tuple[dict[str, str], ...] = (
    {"id": VENUE_TAG_ID, "name": "Music Venue", "type": "urn:tag:category:place"},
    {"id": "urn:tag:category:place:live_music_venue", "name": "Live Music Venue",
     "type": "urn:tag:category:place"},
    {"id": "urn:tag:category:place:concert_hall", "name": "Concert Hall",
     "type": "urn:tag:category:place"},
    {"id": "urn:tag:genre:music:indie_folk", "name": "Indie Folk", "type": "urn:tag:genre:music"},
)
