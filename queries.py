"""
Search keywords for Hidden Gems.

Roblox only returns a handful of games per search, so finding hidden gems means running a LOT
of different searches. This file holds every keyword bank and builds the search plan.

How the plan works
- Tier 0: ~600 hand-picked keywords (trends, TikTok/Shorts hooks, evergreen genres). They run
  on EVERY scan, with two result pages each.
- The "pool": tens of thousands of generated searches (word banks mixed with the naming
  formulas Roblox titles use). A scan runs a WINDOW of the pool starting at a saved cursor,
  then the cursor moves forward. Scan after scan walks through the entire pool, so even short
  scans keep finding new things instead of repeating the same searches.
- During a scan, words and creator names from games that qualify are searched too (see main file).
"""

import functools
import random
import re

# quick/standard/deep run that many searches per scan. "max" runs the entire pool
# (including a 3-letter crawl), which takes hours.
MODES = {"quick": 500, "standard": 3000, "deep": 10000, "max": None}

# TikTok / YouTube Shorts keywords, researched 16 Oct 2026 (TikTok Discover pages for Roblox,
# hooked.so's YouTube Roblox trends, October roundups). Short-form creators drive discovery of
# small games, so this list targets the hooks they film: hide-and-seek variants, party and
# prank games, social deduction, "release day" hype, challenge titles and creator-friendly
# mechanics. A few entries are standard spin-offs of those hooks rather than named in the research.
VIRAL_KEYWORDS = [
    # Hide and seek wave (Meccha Chameleon, Hide vs Drone, Seeker vs Hider, Animal Hospital hide and seek)
    "hide and seek", "hide and seek extreme", "seeker vs hider", "seeker", "hider", "hide vs drone",
    "drone", "blind hide and seek", "paint and seek", "meccha chameleon", "chameleon", "camouflage",
    "prop hunt", "disguise", "hide and seek anomaly", "hide", "seek", "hiding", "found you",
    # TikTok party / prank challenge games (bomb chip, lying game, dumpling duel, don't overfill, guess the number)
    "bomb", "bomb chip", "chip", "lying game", "liar", "lie", "dumpling", "squishy", "duel",
    "dont overfill", "overfill", "guess the number", "guess", "would you rather", "truth or dare",
    "prank", "trolling", "challenge", "impossible", "hardest", "funniest", "underrated",
    "party", "party game", "minigames", "mini games", "mini game party", "hangout", "niche hangout place",
    # Social deduction / chase
    "murder mystery", "mm2", "sheriff", "murderer", "impostor", "traitor", "werewolf", "mafia",
    "spy", "detective", "suspect", "innocent", "betrayal", "vote", "among us",
    # Release-day and "just dropped" hype
    "out now", "coming soon", "just released", "newly released", "v2", "remake", "reboot",
    "anime finals", "wonderland", "gunfight", "real futbol x", "stampede rush", "stampede",
    "avatar", "airbender", "bending", "elemental", "atla", "rush",
    # Racing / running / viral obstacle
    "jungle rush", "death race", "marathon", "running race", "race", "run", "runner", "sprint",
    "tsunami", "escape tsunami", "tsunami brainrot", "flood", "survive the flood", "lava",
    "floor is lava", "natural disaster", "obby but", "obby carry", "only up", "tower of hell",
    "difficulty chart", "speed run",
    # Spooky / liminal / train tunnel clips
    "the train tunnels", "train", "tunnel", "subway", "elevator", "apartment", "hotel", "motel",
    "school", "hallway", "rooms", "lobby", "poolrooms", "liminal space", "vhs", "analog",
    "found footage", "ghost", "haunted house", "cursed", "creepypasta",
    # Squid game and battle royale hooks
    "squid game", "red light green light", "glass bridge", "marbles", "last to leave",
    "100 players", "99 players", "1v1", "1 vs 100", "battle royale", "last man standing",
    "sniper vs", "gun game", "arena", "deathmatch", "capture the flag",
    # Physics / chaos / "funny clip" games
    "ragdoll", "physics", "crash", "destruction", "bouncy", "trampoline", "slide", "waterslide",
    "shopping cart", "cart ride", "wheelie", "rocket", "launch", "catapult", "cannon",
    "explosion", "wrecking ball", "domino", "marble run", "parkour tag",
    # Luck / RNG / gacha / crate hooks that clip well
    "rng", "luck", "lucky block", "roll", "roll a", "crate", "unbox", "case opening", "gacha",
    "spin", "mutation", "evolve", "evolution", "merge", "merge simulator", "rebirth", "afk",
    "afk simulator", "auto farm", "tapping", "tap", "upgrade", "trade", "trading",
    # Italian brainrot characters and meme hooks
    "tung tung", "sahur", "tralalero", "bombardiro", "crocodilo", "lirili", "chimpanzini",
    "cappuccino", "ballerina", "strawberry elephant", "saturno", "sigma", "skibidi", "rizz",
    "aura farming", "npc", "gyatt", "ohio", "mewing", "looksmax", "67",
    # Climbing / hiking trend
    "climb", "climbing simulator", "mountain", "hike", "mount", "summit", "cliff", "cave",
    # Non-English titles (Indonesia / Brazil / Philippines / LatAm audiences are huge on TikTok
    # and these titles get less competition)
    "indonesia", "indo", "brasil", "brazil", "pinoy", "filipino", "simulador", "jogo", "juego",
    "espanol", "portugues", "turkiye", "russia",
]

# Trending keywords, researched 16 Oct 2026 from live Roblox charts and trend reports
# (RBLXSTAT, Rotrends, Blox Radar, October game roundups). Big hits like "Steal an Egg" are
# too big to pass the visits filter, but searching their names and mechanics surfaces the
# small clones and spin-offs riding the same trend, which is where hidden gems show up.
# These run FIRST so trending matches appear in the web UI early.
TRENDING_KEYWORDS = [
    # "+1" / speed / incremental naming pattern (+1 Speed Keyboard, +1 Stone Skipping...)
    "+1", "plus 1", "+1 speed", "+1 strength", "+1 aura", "+1 tongue escape",
    "+1 stone skipping", "speed keyboard", "+9999 speed", "paint to get rich", "get rich",
    "dig to escape", "stone skipping", "wings", "aura", "strength", "superpower",
    # Steal-and-defend / brainrot (Steal a Brainrot, Steal An Egg, Steal a Car...)
    "steal a", "steal an egg", "break and steal", "steal a brainrot", "steal a car",
    "steal a city", "steal animal", "clone to steal", "brainrot", "brainrot trading",
    "wave of brainrots", "brainrot tycoon", "italian brainrot",
    # Eggs and pets ("... for Eggs" games, Ride A Pet)
    "for eggs", "egg", "eggs", "hatch", "pet", "pets", "ride a pet", "collect rare pets", "adopt",
    # Grow a Garden style idle farming
    "grow a garden", "grow a", "garden", "plant", "harvest", "fruit",
    # Fishing, digging, loot, forging
    "fisch", "fish it", "fish", "fishing", "dig", "storage hunters", "open world", "mine",
    "forge", "loot", "lootbound", "antarctica", "treasure", "hunters",
    # Tycoon twists
    "dropper", "one dropper", "tycoon but", "theme park tycoon", "car dealership tycoon",
    "restaurant tycoon 3", "tycoon simulator",
    # Co-op horror and survival
    "99 nights", "nights in the forest", "doors", "forsaken", "demonology", "mimic",
    "liminal", "backrooms", "night shift", "anomaly", "keep the door locked", "monochrome",
    "specter", "apeirophobia", "dandy's world", "evade", "survive", "hide and seek",
    "monster in the mansion", "mansion", "jumpscare", "escape the", "last stop",
    "death order", "simon says", "violence district", "cheating during testing",
    "scam together", "film animals",
    # Anime
    "jujutsu", "shenanigans", "blue lock", "anime dice", "anime zero", "shadow monarch",
    "solo leveling", "type soul", "anime mine", "anime fighters", "blade ball", "naruto",
    "one piece", "bleach", "assassin",
    # Competitive and sports
    "volleyball", "ball vs ball", "sniper", "bloxstrike", "rivals", "football",
    "goat football", "showdown", "card battles", "prestige",
    # Driving
    "driver's license", "drivers license", "kukirin", "ghost driver", "driving empire",
    "car dealership", "octane", "scooter",
    # Quirky viral hooks
    "fling things", "peel the potato", "search for the needle", "who farted", "troll",
    "carry tower", "fly and carry", "balloons", "cat sitting", "cat-sitting",
    "firefighting", "firefighter", "needle", "potato", "tower",
    # Roleplay and life sims
    "life rp", "horse life", "savannah life", "dinosaur survival", "dress to impress",
    "medical", "gp simulator", "redcliff", "brookhaven", "pizza place", "miraculous",
    # Halloween / autumn seasonal
    "halloween", "pumpkin", "spooky", "trick or treat", "candy", "fall", "autumn", "costume",
    # Upcoming fall releases and their genres
    "prime heroes", "starforged", "scrapbook saga", "nemesis", "fossil force", "caramel",
    "dinosaur", "fossil",
    # Fresh-release hooks (new or recently updated games often say this in the title)
    "update", "release", "new update", "alpha", "beta", "codes", "x2", "event", "playtest",
]

# Evergreen keywords (always-popular genres)
EVERGREEN_KEYWORDS = [
    # Simulators
    "simulator", "idle simulator", "clicker", "incremental", "pet simulator", "business simulator",
    "fishing simulator", "mining simulator", "cooking simulator", "driving simulator",
    # Tycoons
    "tycoon", "business", "factory", "farm", "restaurant tycoon", "hotel tycoon", "mall tycoon",
    "theme park tycoon", "game tycoon",
    # Obbies / parkour
    "obby", "platformer", "parkour", "adventure", "quest", "obstacle", "jumper", "speedrun",
    # Combat / RPG
    "rpg", "mmorpg", "dungeon", "sword", "combat", "boss", "battler", "fighter", "anime",
    # Brainrot / memes
    "brain rot", "shitpost", "meme", "funny", "silly", "chaos", "random", "weird",
    # Roleplay / social
    "roleplay", "rp", "town", "city", "school", "restaurant", "hospital", "police", "military",
    # Racing / sports
    "racing", "car", "driver", "sports", "competition", "soccer", "basketball", "skating",
    # Horror
    "horror", "scary", "creepy", "mystery", "thriller", "haunted", "zombie",
    # Building / creative
    "build", "building", "creative", "sandbox", "construction", "architect", "design",
    # Music
    "music", "rhythm", "dance", "concert", "singing",
    # Strategy / puzzle
    "puzzle", "strategy", "tower defense", "tactics", "solver",
    # Trending
    # Niche
    "cooking", "shop", "escape room", "survival", "mining", "fishing", "hunting",
    "story", "game show", "game dev", "idle",
]

# Lower-priority extras. These only run if the total is still under MAX_QUERIES, so they are
# the first to be cut. Franchise names, retro titles, animals, food/vehicle themes and filler.
EXTRA_KEYWORDS = [
    # Franchise / fan-game hooks (most TikTok-relevant first)
    "sprunki", "digital circus", "poppy playtime", "fnaf", "rainbow friends",
    "five nights", "garten", "incredibox", "sonic", "pokemon", "bluey", "spongebob",
    "minecraft", "mario", "zelda", "geometry dash", "undertale", "deltarune", "terraria",
    "stardew", "hello neighbor",
    # Retro / nostalgia titles
    "classic", "retro", "nostalgia", "2016", "old roblox", "oldschool", "og", "remastered",
    # Animals / cozy creatures that clip well
    "cat", "dog", "capybara", "axolotl", "bunny", "dragon", "unicorn", "frog", "duck", "shark",
    "raccoon", "horse", "wolf", "fox", "hamster", "monkey", "bear",
    # Creator-friendly "watch me" mechanics
    "obby but", "but every", "but you", "but its", "ruin", "destroy", "survive", "rescue",
    "lifeguard", "babysitter", "daycare", "prison", "heist", "jail", "bank", "cops and robbers",
    "zoo", "aquarium", "museum", "gym", "bakery", "pizza", "ice cream", "bubble tea", "boba",
    "sushi", "burger", "carwash", "garage", "mechanic", "truck", "bus", "train simulator",
    "plane", "airport", "pilot", "airline", "cruise", "boat", "build a boat", "submarine",
    # Generic filler words
    "trending", "viral", "new", "hot", "popular", "best", "top",
    "game", "roblox", "fun", "play", "multiplayer",
]

# =========================== QUERY GENERATOR ===========================
# Roblox titles follow naming formulas ("+1 Speed ...", "Steal a ...", "... Simulator",
# "Escape the ...", "Grow a ...", "... for Eggs"). Instead of typing thousands of searches,
# the script builds them by mixing word banks with those formulas.

GENRES = [
    "obby", "tycoon", "simulator", "rpg", "tower defense", "survival", "horror", "roleplay",
    "fighting", "shooter", "racing", "parkour", "clicker", "idle", "sandbox", "adventure",
    "puzzle", "strategy", "party", "battle royale", "sports", "hangout", "social deduction",
    "co-op", "pvp", "boss fight", "open world", "mmo", "card game", "gacha", "rng", "defense",
    "escape", "chase", "stealth", "base building", "farming", "trading", "minigames", "arena",
]

# Themes taken from the 2026 trend research (steal-and-defend, eggs, hide and seek, tsunami...)
TREND_THEMES = [
    "brainrot", "anime", "egg", "pet", "garden", "fishing", "hide and seek", "tsunami", "liminal",
    "backrooms", "halloween", "pumpkin", "cat", "dinosaur", "dragon", "zombie", "ninja", "pirate",
    "space", "ocean", "candy", "boba", "climbing", "squid game", "lava", "flood", "bomb", "prank",
    "hospital", "school", "mansion", "forest", "night", "chameleon", "drone", "hunter", "monster",
    "ghost", "aura", "speed", "strength", "luck", "mutation", "steal", "carry", "tower", "cursed",
    "cozy", "capybara", "italian", "meme",
]

# For the "+1 ..." / "... for Eggs" naming wave
STAT_WORDS = [
    "speed", "strength", "jump", "aura", "tongue", "wings", "luck", "damage", "size", "power",
    "health", "level", "rebirth", "muscle", "reach", "fly", "gravity", "stamina", "range",
    "defense", "crit", "magic", "mana", "money", "cash", "gems", "coins", "xp", "rank", "fame",
    "followers", "stone skipping", "loot", "keyboard", "superpower", "balloons", "height", "hair",
    "hug", "bounce", "swim", "dash", "teleport", "shield", "scream",
]
STAT_TEMPLATES = ["+1 {x}", "+1 {x} escape", "+1 {x} tower", "+1 {x} simulator", "+1 {x} obby",
                  "+1 {x} for eggs", "{x} for eggs", "{x} escape"]

CREATURES = [
    "cat", "dog", "dragon", "dinosaur", "shark", "frog", "duck", "bunny", "capybara", "axolotl",
    "panda", "penguin", "monkey", "wolf", "fox", "bear", "horse", "unicorn", "spider", "snake",
    "octopus", "crab", "turtle", "bee", "ant", "chicken", "cow", "pig", "sheep", "llama",
    "raccoon", "hamster", "tiger", "lion", "elephant", "giraffe", "crocodile", "goblin", "zombie",
    "ghost", "vampire", "werewolf", "mummy", "skeleton", "witch", "alien", "robot", "mermaid",
    "fairy", "ninja", "pirate", "knight", "samurai", "wizard", "cowboy", "caveman", "giant",
    "titan", "monster", "demon", "angel", "slime", "mutant", "ogre", "yeti", "bigfoot", "kraken",
    "phoenix", "golem", "imp", "clown", "scarecrow", "doll", "puppet", "cryptid",
]
CREATURE_T = ["{x} simulator", "{x} tycoon", "steal a {x}", "be a {x}", "become a {x}", "{x} rng",
              "escape {x}", "survive {x}", "{x} tower defense", "hide from {x}", "{x} rp",
              "feed the {x}", "ride a {x}", "{x} obby", "{x} life", "{x} hunt", "{x} legends"]

FOODS = [
    "pizza", "burger", "taco", "sushi", "ramen", "donut", "cookie", "cake", "ice cream", "candy",
    "chocolate", "boba", "coffee", "banana", "strawberry", "watermelon", "pumpkin", "cheese",
    "pickle", "potato", "noodle", "sandwich", "popcorn", "pancake", "hot dog", "fries", "egg",
    "bread", "cupcake", "apple", "carrot", "mushroom",
]
FOOD_T = ["{x} tycoon", "{x} simulator", "{x} factory", "make a {x}", "{x} shop", "grow a {x}",
          "steal a {x}", "{x} rng", "{x} clicker", "{x} restaurant", "{x} obby", "{x} empire"]

PLACES = [
    "school", "hospital", "hotel", "mall", "airport", "zoo", "farm", "garden", "island", "castle",
    "jungle", "forest", "cave", "mine", "ocean", "space station", "moon", "volcano", "desert",
    "snow", "city", "town", "village", "prison", "factory", "bank", "restaurant", "cafe", "bakery",
    "gym", "library", "museum", "subway", "train station", "elevator", "apartment", "mansion",
    "backrooms", "house", "bridge", "pool", "beach", "camp", "bunker", "lab", "laboratory",
    "supermarket", "gas station", "theme park", "carnival", "cemetery", "lighthouse", "swamp",
    "mountain", "temple", "pyramid", "dungeon",
]
PLACE_T = ["{x} tycoon", "{x} simulator", "escape the {x}", "survive the {x}", "{x} roleplay",
           "{x} rp", "{x} horror", "{x} obby", "{x} life", "{x} survival", "build a {x}",
           "{x} defense", "{x} story", "{x} hide and seek", "work at a {x}", "{x} city"]

THINGS = [
    "car", "truck", "bus", "plane", "boat", "ship", "rocket", "helicopter", "bike", "scooter",
    "skateboard", "cart", "tank", "sword", "gun", "bow", "pickaxe", "shovel", "fishing rod",
    "bomb", "balloon", "ball", "dice", "key", "door", "mirror", "camera", "phone", "laptop",
    "keyboard", "chair", "bed", "toilet", "ladder", "trampoline", "slide", "cannon", "drone",
    "robot", "backpack", "umbrella", "lantern", "crown",
]
THING_T = ["{x} simulator", "{x} tycoon", "build a {x}", "{x} crash", "{x} race", "{x} obby",
           "{x} tower", "steal a {x}", "{x} battles", "{x} rng", "{x} clicker", "{x} wars"]

ACTIONS = [
    "dig", "mine", "fish", "farm", "cook", "build", "craft", "forge", "hatch", "escape",
    "survive", "hide", "run", "climb", "jump", "fly", "swim", "drive", "race", "fight",
    "defend", "collect", "sell", "trade", "merge", "upgrade", "spin", "roll", "punch", "throw",
    "kick", "slap", "smash", "break", "carry", "lift", "push", "pull", "tap", "click", "sneak",
    "rescue", "deliver", "catch", "feed",
]
ACTION_T = ["{x} simulator", "{x} tycoon", "{x} for eggs", "{x} to escape", "{x} a brainrot",
            "+1 {x}", "{x} tower", "{x} race", "{x} obby", "{x} wars", "{x} master",
            "{x} legends", "{x} challenge", "{x} and steal", "{x} to get rich"]

# Title decorations that small developers add (tags before the name, words after it)
TAG_HOOKS = ["[UPDATE]", "[NEW]", "[RELEASE]", "[BETA]", "[ALPHA]", "[X2]", "NEW"]
WORD_HOOKS = [
    "codes", "2", "v2", "remastered", "classic", "legends", "world", "online", "ultimate",
    "extreme", "mega", "super", "infinite", "endless", "chaos", "madness", "wars", "arena",
    "academy", "city", "island", "kingdom", "simulator", "tycoon",
]

# Words ignored when learning new search terms from live game names
STOPWORDS = {
    "the", "and", "for", "with", "from", "your", "you", "new", "update", "upd", "codes", "code",
    "beta", "alpha", "release", "roblox", "game", "games", "free", "best", "how", "are", "not",
    "all", "get", "out", "now", "its", "this", "that", "can", "has", "was", "one", "two", "event",
    "test", "testing", "official", "studio", "studios", "group", "dev", "devs", "play", "playing",
}


def _fill(templates, words):
    return [t.format(x=w) for w in words for t in templates]


# ======================= BIGGER WORD BANKS =======================
ADJECTIVES = [
    "cursed", "tiny", "giant", "haunted", "golden", "rainbow", "neon", "cozy", "spooky", "mega",
    "ultra", "secret", "hidden", "lost", "ancient", "frozen", "burning", "toxic", "magic", "mystic",
    "royal", "evil", "cute", "funny", "extreme", "impossible", "endless", "infinite", "crazy", "insane",
    "angry", "sleepy", "hungry", "lazy", "fast", "slow", "strong", "weak", "tough", "epic",
    "legendary", "mythic", "divine", "dark", "light", "shadow", "cosmic", "galactic", "atomic", "nuclear",
    "robotic", "cyber", "pixel", "blocky", "tiny", "mini", "micro", "mega", "super", "hyper",
    "turbo", "rocket", "flying", "floating", "sinking", "falling", "rolling", "spinning", "bouncing", "sticky",
    "slimy", "fuzzy", "fluffy", "spiky", "shiny", "glowing", "invisible", "broken", "fake", "real",
    "last", "first", "final", "ultimate", "perfect", "random", "weird", "strange", "creepy", "scary",
    "silly", "goofy", "dumb", "smart", "rich", "poor", "free", "lucky", "unlucky", "wild",
    "feral", "tame", "stray", "lonely", "friendly", "mean", "grumpy", "happy", "sad", "emotional",
    "midnight", "sunset", "sunrise", "stormy", "snowy", "rainy", "sunny", "foggy", "windy", "icy",
    "fiery", "watery", "earthy", "airy", "electric", "magnetic", "sonic", "silent", "loud", "quiet",
    "overpowered", "underrated", "forgotten", "abandoned", "ruined", "sacred", "forbidden", "stolen", "borrowed", "cloned",
]

EXTRA_NOUNS = [
    "king", "queen", "princess", "prince", "baby", "granny", "grandpa", "teacher", "student", "boss",
    "hero", "villain", "thief", "cop", "agent", "cyborg", "android", "mech", "army", "kingdom",
    "empire", "planet", "galaxy", "portal", "maze", "labyrinth", "ruins", "treasure", "gold", "diamond",
    "crystal", "gem", "emerald", "ruby", "shield", "armor", "potion", "spell", "wand", "book",
    "scroll", "map", "compass", "lock", "chest", "nest", "ranch", "barn", "flower", "tree",
    "river", "lake", "wave", "storm", "thunder", "lightning", "fire", "ice", "rain", "cloud",
    "sun", "star", "comet", "ufo", "meteor", "volcano", "earthquake", "tornado", "flood", "avalanche",
    "bubble", "balloon", "rainbow", "candy", "lollipop", "gummy", "jelly", "marshmallow", "waffle", "toast",
    "soup", "salad", "steak", "bacon", "sausage", "chicken nugget", "milkshake", "smoothie", "tea", "juice",
    "soda", "pretzel", "bagel", "muffin", "brownie", "pie", "jam", "honey", "peanut", "corn",
    "tomato", "onion", "garlic", "lemon", "orange", "grape", "cherry", "peach", "mango", "pineapple",
    "coconut", "avocado", "broccoli", "pepper", "toy", "teddy bear", "lego", "puzzle", "blocks", "slime",
    "bed", "couch", "table", "lamp", "clock", "radio", "tv", "camera", "guitar", "piano",
    "drum", "microphone", "speaker", "headphones", "controller", "joystick", "arcade", "console", "robot dog", "hoverboard",
    "skateboard", "roller coaster", "ferris wheel", "carousel", "swing", "playground", "treehouse", "tent", "campfire", "cabin",
    "submarine", "airship", "train", "tram", "taxi", "ambulance", "fire truck", "police car", "tractor", "bulldozer",
    "crane", "excavator", "forklift", "snowmobile", "jet ski", "kayak", "raft", "canoe", "yacht", "ferry",
]

JOBS = [
    "chef", "doctor", "nurse", "farmer", "miner", "fisherman", "police officer", "firefighter", "pilot",
    "teacher", "builder", "thief", "ninja", "detective", "lifeguard", "babysitter", "barista", "baker",
    "waiter", "cashier", "mechanic", "plumber", "electrician", "gardener", "janitor", "driver", "trucker",
    "astronaut", "scientist", "vet", "dentist", "pharmacist", "photographer", "streamer", "youtuber",
    "influencer", "model", "singer", "dancer", "actor", "director", "artist", "painter", "sculptor",
    "librarian", "banker", "lawyer", "judge", "soldier", "sailor", "pirate", "cowboy", "knight", "wizard",
    "blacksmith", "alchemist", "hunter", "ranger", "zookeeper", "park ranger", "delivery driver", "pizza delivery",
]
JOB_T = ["be a {x}", "become a {x}", "{x} simulator", "{x} tycoon", "{x} life", "{x} rp", "work as a {x}", "{x} job"]

SPORTS = [
    "soccer", "football", "basketball", "baseball", "tennis", "golf", "hockey", "volleyball", "boxing",
    "wrestling", "mma", "karate", "skateboarding", "surfing", "skiing", "snowboarding", "swimming", "diving",
    "archery", "bowling", "darts", "pool", "billiards", "drifting", "motocross", "bmx", "cycling", "running",
    "gymnastics", "cricket", "rugby", "badminton", "ping pong", "table tennis", "dodgeball", "kickball",
    "handball", "fencing", "rock climbing", "skating", "rollerblading", "cheerleading", "dance battle",
]
SPORT_T = ["{x} simulator", "{x} tycoon", "{x} legends", "{x} obby", "{x} championship", "{x} tournament", "{x} 2", "{x} clicker"]

ANIME_AND_FRANCHISES = [
    "naruto", "boruto", "one piece", "dragon ball", "bleach", "jujutsu kaisen", "demon slayer", "attack on titan",
    "my hero academia", "solo leveling", "blue lock", "haikyuu", "chainsaw man", "tokyo ghoul", "hunter x hunter",
    "fairy tail", "sword art online", "black clover", "fullmetal alchemist", "death note", "one punch man",
    "mob psycho", "spy x family", "fire force", "dr stone", "vinland saga", "jojo", "gintama", "cowboy bebop",
    "evangelion", "code geass", "re zero", "konosuba", "overlord", "made in abyss", "dandadan", "kaiju no 8",
    "sakamoto days", "frieren", "oshi no ko", "your name", "spirited away", "totoro", "pokemon", "digimon",
    "yugioh", "sailor moon", "dragon quest", "final fantasy", "kingdom hearts", "persona", "genshin", "honkai",
    "zelda", "mario", "sonic", "kirby", "metroid", "fire emblem", "splatoon", "animal crossing", "among us",
    "fortnite", "minecraft", "terraria", "undertale", "hollow knight", "stardew valley", "poppy playtime",
    "five nights at freddys", "garten of banban", "rainbow friends", "amazing digital circus", "sprunki",
    "skibidi toilet", "squid game", "stranger things", "harry potter", "star wars", "marvel", "avengers",
    "spider man", "batman", "superman", "transformers", "ben 10", "teen titans", "gravity falls", "adventure time",
    "steven universe", "spongebob", "bluey", "peppa pig", "paw patrol", "cocomelon", "hello kitty", "sanrio",
    "gacha life", "my singing monsters", "brawl stars", "clash royale", "subway surfers", "temple run",
    "geometry dash", "flappy bird", "cut the rope", "plants vs zombies", "angry birds", "fruit ninja",
]
FRANCHISE_T = ["{x} tycoon", "{x} simulator", "{x} rpg", "{x} fighters", "{x} tower defense", "{x} legends", "{x} battlegrounds", "{x} obby"]

SEASONS = [
    "halloween", "christmas", "winter", "summer", "spring", "autumn", "thanksgiving", "easter", "valentines",
    "new year", "birthday", "carnival", "summer camp", "snow day", "black friday", "spooky season", "pumpkin patch",
]

# Non-English words (Portuguese, Spanish, Indonesian, Filipino, Turkish, French, German). Games with
# non-English titles face far less competition in search.
FOREIGN_WORDS = [
    "simulador", "fazenda", "cidade", "escola", "corrida", "jogo", "aventura", "fuga", "fantasma", "tubarao",
    "granja", "ciudad", "escuela", "carrera", "juego", "pueblo", "zombi", "cocina", "tienda", "hospital",
    "kota", "sekolah", "balap", "hantu", "permainan", "petualangan", "desa", "pasar", "kereta", "mobil",
    "laro", "bayan", "paaralan", "karera", "multo", "tindahan", "bukid",
    "oyun", "araba", "okul", "sehir", "hayalet", "macera",
    "ville", "ecole", "course", "jeu", "fantome",
    "stadt", "schule", "rennen", "spiel", "geist",
]
FOREIGN_T = ["{x}", "{x} roblox", "{x} simulator", "{x} tycoon", "{x} obby", "{x} rp", "simulador de {x}", "{x} brasil"]

NUMBERS = [2, 3, 5, 10, 24, 50, 99, 100, 500, 1000, 9999]
NUMBER_T = ["{n} nights", "{n} days", "{n} players", "{n} lives", "{n} rounds", "{n} levels", "{n} stages",
            "survive {n} minutes", "x{n}", "+{n}", "{n} brainrots", "{n} eggs", "{n} pets"]

NOUN_SUFFIX_T = ["{x} simulator", "{x} tycoon", "{x} obby", "{x} rng", "{x} defense", "{x} life", "{x} rp",
                 "{x} legends", "{x} world", "{x} online", "{x} wars", "{x} adventure", "{x} story",
                 "{x} battle", "{x} arena", "{x} academy", "{x} factory", "{x} clicker"]
NOUN_VERB_T = ["carry a {x}", "steal a {x}", "feed a {x}", "ride a {x}", "catch a {x}", "escape from {x}",
               "defend the {x}", "build a {x}", "find the {x}", "save the {x}", "throw a {x}", "launch a {x}",
               "grow a {x}", "raise a {x}", "be a {x}", "become a {x}", "survive a {x}", "fight a {x}"]


def _norm(q):
    return q if q.startswith("[") else " ".join(q.lower().split())


def _ok(q):
    """Skip silly repeats such as 'simulator simulator'."""
    words = q.lower().split()
    return len(words) == len(set(words)) and 2 <= len(q) <= 60


@functools.lru_cache(maxsize=1)
def base_keywords():
    """Tier 0: the hand-picked keywords that run on every scan."""
    return list(dict.fromkeys(VIRAL_KEYWORDS + TRENDING_KEYWORDS + EVERGREEN_KEYWORDS + EXTRA_KEYWORDS))


@functools.lru_cache(maxsize=2)
def _pool(extended=False):
    """The big generated list. Deterministic (fixed seed) so the saved cursor always means the same thing."""
    rng = random.Random(2026)
    nouns = list(dict.fromkeys(CREATURES + FOODS + PLACES + THINGS + EXTRA_NOUNS))

    # Tier A: the strongest mixes first (they are what earlier versions ran)
    a = []
    a += _fill(STAT_TEMPLATES, STAT_WORDS)
    a += _fill(CREATURE_T, CREATURES)
    a += _fill(FOOD_T, FOODS)
    a += _fill(PLACE_T, PLACES)
    a += _fill(THING_T, THINGS)
    a += _fill(ACTION_T, ACTIONS)
    a += [f"{theme} {genre}" for theme in TREND_THEMES for genre in GENRES]
    a += [f"{x} {y}" for i, x in enumerate(TREND_THEMES) for y in TREND_THEMES[i + 1:]]
    core = base_keywords()[:130]
    a += [f"{tag} {k}" for k in core for tag in TAG_HOOKS]
    a += [f"{k} {w}" for k in core for w in WORD_HOOKS]
    rng.shuffle(a)

    # Tier B: new, bigger banks (shuffled together so any window mixes many themes)
    b = []
    b += _fill(NOUN_SUFFIX_T, nouns)
    b += _fill(NOUN_VERB_T, nouns)
    b += _fill(JOB_T, JOBS)
    b += _fill(SPORT_T, SPORTS)
    b += _fill(FRANCHISE_T, ANIME_AND_FRANCHISES)
    b += [f"{ad} {g}" for ad in ADJECTIVES for g in GENRES]
    b += [f"{ad} {n}" for ad in ADJECTIVES for n in rng.sample(nouns, min(60, len(nouns)))]
    b += [f"{s} {n}" for s in SEASONS for n in rng.sample(nouns, 50)] + [f"{s} {g}" for s in SEASONS for g in GENRES]
    b += [f"{x} {y}" for i, x in enumerate(GENRES) for y in GENRES[i + 1:]]
    b += _fill(FOREIGN_T, FOREIGN_WORDS)
    b += [t.format(n=n) for n in NUMBERS for t in NUMBER_T]
    b += [f"99 nights {p}" for p in PLACES] + [f"nights in the {p}" for p in PLACES]
    b += [f"{c} {p}" for c in CREATURES for p in PLACES]
    # two-letter crawl: random-ish results that surface games no keyword would
    letters = "abcdefghijklmnopqrstuvwxyz"
    b += [x + y for x in letters for y in letters + "0123456789"]
    rng.shuffle(b)

    pool_list = a + b
    if extended:   # 3-letter crawl, only for the "max" mode
        c = [x + y + z for x in letters for y in letters for z in letters]
        rng.shuffle(c)
        pool_list += c

    out, seen = [], {_norm(q).lower() for q in base_keywords()}
    for q in pool_list:
        q = _norm(q)
        if _ok(q) and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def build_plan(mode, cursor=0):
    """Return (plan, info). plan is the ordered list of (query, pages) to run for this scan.
    info describes the window: {"base", "pool", "window", "start", "rotates"}.
    The caller advances its saved cursor so the next scan continues where this one stopped."""
    cap = MODES.get(mode, MODES["deep"])
    base = base_keywords()
    if cap is None:                                  # "max": everything, no rotation
        pool = _pool(True)
        plan = [(q, 2) for q in base] + [(q, 1) for q in pool]
        return plan, {"base": len(base), "pool": len(pool), "window": len(pool), "start": 0, "rotates": False}

    pool = _pool(False)
    if cap <= len(base):                             # "quick": hand-picked keywords only
        plan = [(q, 2) for q in base[:cap]]
        return plan, {"base": len(plan), "pool": len(pool), "window": 0, "start": 0, "rotates": False}

    n = min(cap - len(base), len(pool))
    start = cursor % len(pool)
    window = (pool + pool)[start:start + n]
    plan = [(q, 2) for q in base] + [(q, 1) for q in window]
    return plan, {"base": len(base), "pool": len(pool), "window": n, "start": start, "rotates": n < len(pool)}



def name_terms(name):
    """Searchable words and word pairs from a game name, e.g. '[UPD] Lava Cat Escape' ->
    {'lava', 'cat', 'escape', 'lava cat', 'cat escape'}."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name.lower())
    words = [w for w in re.findall(r"[a-z][a-z']{2,}", text) if w not in STOPWORDS]
    terms = set(words)
    terms.update(f"{a} {b}" for a, b in zip(words, words[1:]))
    return terms


