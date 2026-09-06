"""
bulletin_constants.py — Shared constants for bulletin name extraction.

Extracted from run_bulletin_scraper.py so fallback parsers and other modules
can import them without pulling in the entire scraper.
"""

# Comprehensive list of positional roles found in bulletin staff sections
STAFF_ROLES = (
    # Clergy roles
    r"Pastor|Associate Pastor|Parochial Vicar|Parochial Administrator"
    r"|Administrator|Priest|Rector|Chaplain|Celebrant"
    # Deacon roles
    r"|Permanent Deacon|Transitional Deacon"
    # Parish staff
    r"|Business Manager|Business Mgr\.|Office Manager|Parish Manager"
    r"|Parish Secretary|Parish Administrator|Administrative Assistant"
    r"|Bookkeeper|Assistant Bookkeeper|Receptionist"
    r"|Compliance(?:/Acct\.\s*Asst\.)?|Compliance Officer"
    # Education
    r"|Director of Religious (?:Education|Ed)|Religious Ed(?:ucation)?"
    r"|Director of Faith Formation|Faith Formation Director"
    r"|School Principal|School Secretary|Principal"
    r"|Director of Youth Ministry|Youth Minister|Youth Director"
    r"|Director of Music|Music Director|Music Minister"
    r"|Liturgy Director|Liturgist|Worship Director"
    # Facilities
    r"|Maintenance|Custodian|Facilities Manager|Facilities Director"
    r"|Maintenance Tech|Groundskeeper|Sexton"
    # Ministry roles
    r"|Director|Coordinator|Minister|Moderator"
    r"|RCIA Director|RCIA Coordinator"
    r"|Sacristan|Organist|Cantor|Choir Director"
    r"|Stewardship Director|Communications Director"
    r"|Hispanic Ministry|Spanish Ministry"
    # Council/board roles
    r"|Chairman|Co-Chairman|Chairperson|Co-Chair"
    r"|Vice Chairman|Vice Chairperson"
    r"|President|Vice President"
    r"|Secretary|Treasurer"
    r"|Grand Knight|Deputy Grand Knight"
    r"|Financial Secretary|Membership Director"
    r"|ASCS Principal"
)

# Ministry-specific roles for Pattern 3 extraction
MINISTRY_ROLES = (
    r"Altar Servers?|Eucharistic Ministers?|Lectors?|Readers?"
    r"|Ushers?(?:/Greeters?)?|Greeters?|Sacristans?"
    r"|Gift Shop|Hospital Euch(?:aristic)?\.?\s*Ministers?"
    r"|Jail Ministry|Knights? of Columbus|Ladies\s+Guild"
    r"|Linens|Marriage Preparation|Money Counters?"
    r"|Prayer Garden|Music Ministry|Choir"
    r"|Baptism Class|Hispanic Spiritual Dir(?:ector)?"
    r"|(?:You Are )?Not Alone|St\.?\s*Vincent de Paul"
    r"|Religious Education|RCIA|Compliance Officer"
    r"|Homebound Euch(?:aristic)?\.?\s*Ministers?"
)

# Honorific prefixes recognized in bulletin names
HONORIFIC_TITLES = {
    "Fr.",
    "Father",
    "Rev.",
    "Reverend",
    "Msgr.",
    "Monsignor",
    "Dcn.",
    "Deacon",
    "Sr.",
    "Sister",
    "Br.",
    "Brother",
    "Dr.",
    "Bishop",
    "Archbishop",
}

# Regex pattern for matching honorific prefixes
HONORIFIC_PATTERN = (
    r"(?:Fr\.|Father|Rev\.|Reverend|Msgr\.|Monsignor|Dcn\.|Deacon"
    r"|Sr\.|Sister|Br\.|Brother|Dr\.|Bishop|Archbishop)"
)

# Common words that look like names but aren't
FALSE_POSITIVE_NAMES = {
    "Holy Spirit",
    "Holy Family",
    "Holy Cross",
    "Holy Rosary",
    "Holy Trinity",
    "Sacred Heart",
    "Blessed Sacrament",
    "Blessed Mother",
    "Blessed Virgin",
    "Our Lady",
    "Our Father",
    "Jesus Christ",
    "Holy Name",
    "Good Shepherd",
    "Holy Communion",
    "First Communion",
    "Daily Mass",
    "Sunday Mass",
    "Mass Times",
    "Mass Intentions",
    "Divine Mercy",
    "Eternal Rest",
    "Saint Joseph",
    "Saint Patrick",
    "Saint Mary",
    "Saint Peter",
    "Saint Paul",
    "Saint Michael",
    "Saint Francis",
    "Saint Thomas",
    "Saint Elizabeth",
    "Palm Sunday",
    "Good Friday",
    "Easter Sunday",
    "Ash Wednesday",
    "Office Hours",
    "Parish Office",
    "Faith Formation",
    "Religious Education",
    "Social Media",
    "Weekly Bulletin",
    "Parish Life",
    "Parish Council",
    "Ministry Schedule",
    "Altar Society",
    "Church Bulletin",
    "North America",
    "South America",
    "New York",
    "New Jersey",
    "New Mexico",
    "Al Smith",
    "Catholic Church",
    "United States",
    "Pope Francis",
    "Dear Parishioners",
    "Dear Friends",
    "For More",
    "Please Contact",
    "High School",
    "Middle School",
    "Sign Up",
    "Last Week",
    "Next Week",
    "This Week",
    "Thank You",
    "God Bless",
    "Weekday Masses",
    "Altar Servers",
    "Eucharistic Ministers",
    "Music Director",
    "Prayer Tree",
    "Table Rentals",
    "Holy Hour",
    "Vincent De Paul",
    "De Paul",
    "Corpus Christi",
    "Stations Cross",
    "Bible Study",
    "Choir Practice",
    "Food Bank",
    "Soup Kitchen",
    "Thrift Store",
    "Office Manager",
    "Business Manager",
    "Facilities Manager",
    "Religious Ed",
    "Youth Minister",
    "Choir Director",
    "Maintenance Director",
    "Athletic Director",
    "Pro Life",
    "Right Life",
    # Bulletin structural/calendar phrases that look like names
    "Ordinary Time",
    "By Appointment",
    "Job Opportunity",
    "Assembly Mtg",
    "Council Mtg",
    "Degree Exemplification",
    "Money Counters",
    "Compliance Officer",
    "Mercy Chaplet",
    "Del Tiempo",
    "Domingo Del",
    "Consejo Matrimonial",
    "Grand Knight",
    "Deputy Grand",
    "Hospital Euch",
    "Hispanic Spiritual",
    "Tech I",
    "Tech II",
    # Common truncated/merged column artifacts from PDF extraction
    "Are Not",
    "You Are",
    "Are Not Alone",
    "Anderson Gift",
    "Business Mgr",
    "The Romo",
    "Of Jensen",
    # Top false positive phrases found in data analysis (751K names, 6 states)
    "New Year",
    "Immaculate Conception",
    "Columbus Council",
    "Finance Council",
    "Pastoral Council",
    "Pope Leo",
    "Food Pantry",
    "Fish Fry",
    "All Souls",
    "Wedding Anniversary",
    "Second Vatican Council",
    "Deceased Members",
    "Volunteers Needed",
    "All Souls Day",
    "Lord Jesus Christ",
    "The Knights",
    "Paul Society",
    "May God",
    "Presbyteral Council",
    "Lord Jesus",
    "Good News",
    "Administrative Assistant",
    "The St",
    "Special Intention",
    "Memorial Day",
    "Jubilee Year",
    "Labor Day",
    "Virgin Mary",
    "Feast Day",
    "World Day",
    "Open House",
    "St Mary",
    "Jordan River",
    "Bake Sale",
    "Shawl Ministry",
    "Pancake Breakfast",
    "Latin America",
    "Old Testament",
    "The Lord",
    "Happy New Year",
    "Heavenly Father",
    "Thomas Aquinas",
    "Retirement Fund",
    "First Reconciliation",
    "Diocesan Council",
    "First Reading",
    "Place Your Ad",
    "Safe Environment",
    "Thanksgiving Day",
    "Rice Bowl",
    "The Diocese",
    "Respect Life",
    "Immaculate Heart",
    "The Epiphany",
    "Mailing Address",
    "Poor Souls",
    "Second Reading",
    "Main Street",
    "Columbus Meeting",
    "Extraordinary Minister",
    "Faithful Departed",
    "Life Activities",
    "New Testament",
    "Property Manager",
    "Development Manager",
    "Case Managers",
    "Sun Rehearsal",
    "English Ministry",
    "Brother Knight",
    # Bulletin ad/event junk that passes word-level filters
    "Auto Body",
    "Auto Repair",
    "Auto Insurance",
    "Fall Alert",
    "Craft Beer",
    "Beer Tent",
    "Beer Dance",
    "Wine Bar",
    "Wine Pull",
    "Ice Cream",
    "More Info",
    "Stay Connected",
    "Pork Sausage",
    "Fried Chicken",
    "Chicken Strips",
    "Cake Donation",
    "Smart Roofing",
    "Smart Roof",
    "Smart Driver",
    "Pizza Villa",
    "Sports App",
    "Ascension App",
    "Suggested Donation",
    "Contribution Statement",
    "Contribution Statements",
    "Spring Alpha Session",
    "Generation To Generation",
    "Doyle Vocal Quartet",
    "Vocal Quartet",
    "Blood Drive",
    "Craft Bazaar",
    # Spanish/Latin liturgical phrases
    "Primera Comuni",
    "Primera Comunion",
    "La Primera Comuni",
    "La Cuaresma",
    "El Evangelio",
    "Sacrosanctum Concilium",
    "Nueve Domingos",
    "El Comit",
    "Arroz La Cuaresma",
    # Phrases using 'Will' and 'Christian' that aren't names
    "Will Be",
    "Will Not",
    "Will Have",
    "Will Take",
    "Christian Education",
    "Christian Formation",
    "Christian Initiation",
    "Christian Service",
    "Christian Community",
    "Christian Life",
    # Additional false-positive phrases (org names, bulletin phrases)
    "Church Name",
    "Parish Name",
    "Bulletin Sponsor",
    "Weekly Collection",
    "Mass Schedule",
    "Youth Group",
    "Knights Columbus",
    "Ladies Auxiliary",
    "Sanctuary Lamp",
    "Rest Peace",
    # Top false positives from data analysis (2026-03-25)
    "Precious Blood",
    "Canon Law",
    "Mardi Gras",
    "Faith Forma",
    "Mount Carmel",
    "Roman Missal",
    "Young People",
    "Ascension Press",
    "Supreme Court",
    "Little Flower",
    "La Crosse",
    "Columbus Free Throw",
    "King Herod",
    "Phone Fax",
    "Bus Driver",
    "Texas Roadhouse",
    "Adult Faith",
    "Gospel Meditation",
    "Spiritual Direction",
    "Spring Work",
    "Parish Fund",
    "In Residence",
    "Same Day",
    "Topsoil Mulch",
    "Parish App",
    "Fulton Sheen",
    "Fulton J. Sheen",
    "Martin Luther King",
    "John Muir",
    "Every Friday",
}
