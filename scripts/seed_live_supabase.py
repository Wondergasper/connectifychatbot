import logging
from supabase import create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("connectify.seed")

SUPABASE_URL = "https://aipiataysjncillnkcmm.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFpcGlhdGF5c2puY2lsbG5rY21tIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTE3Njg0NiwiZXhwIjoyMDk0NzUyODQ2fQ."
    "lulixNYx3lWUp_idmxSd9yNEj2UTyVLmiiqAz7Tcd2g"
)

client = create_client(SUPABASE_URL, SUPABASE_KEY)


def main():
    logger.info("Connecting to Supabase to seed initial categories and services...")

    # 1. Seed Categories
    categories = [
        {"name": "Plumbing", "description": "Pipes, boreholes, leak repairs, bathroom and kitchen fittings", "is_active": True},
        {"name": "Electrical", "description": "Home and commercial electrical wiring, inverters and distribution panels", "is_active": True},
        {"name": "AC & Refrigeration", "description": "Air conditioner servicing, installation, gas refill, and fridge repairs", "is_active": True},
        {"name": "Generator Repair", "description": "Diesel and petrol generator maintenance, carburetors, and overhaul", "is_active": True},
        {"name": "Home & Office Clean", "description": "Deep cleaning, post-construction cleaning, and fumigation services", "is_active": True},
        {"name": "Carpentry", "description": "Custom furniture, cabinet fittings, door repairs, and wood craftsmanship", "is_active": True},
        {"name": "Painting", "description": "Interior/exterior house painting, POP installation, and screeding", "is_active": True},
    ]

    for cat in categories:
        try:
            client.from_("categories").upsert(cat, on_conflict="name").execute()
            logger.info("Category [%s] ready.", cat["name"])
        except Exception as e:
            logger.error("Category [%s] error: %s", cat["name"], e)

    # 2. Seed Services for provider John James
    prov_res = client.from_("app_users").select("id, name").eq("role", "provider").limit(1).execute()
    if prov_res.data:
        prov_id = prov_res.data[0]["id"]
        prov_name = prov_res.data[0]["name"]
        logger.info("Linking services to verified provider: %s (%s)", prov_name, prov_id)

        # Check existing services
        existing_services = client.from_("services").select("name").eq("provider_id", prov_id).execute()
        existing_names = {s["name"] for s in (existing_services.data or [])}

        services = [
            {
                "provider_id": prov_id,
                "name": "Expert Plumbing & Leak Solutions",
                "category": "Plumbing",
                "description": "Certified residential and commercial plumbing repairs, pipe installation, water heater setup, and drainage clearing.",
                "price": 6000.00,
                "price_type": "hourly",
                "duration_minutes": 60,
                "location": {"address": "Ikeja, Lagos", "city": "Ikeja", "state": "Lagos"},
                "rating_average": 4.9,
                "rating_count": 28,
                "is_active": True,
            },
            {
                "provider_id": prov_id,
                "name": "Prompt Generator Mechanic & Overhaul",
                "category": "Generator Repair",
                "description": "Professional servicing for Mikano, Perkins, Lutian, and Firman generators across Lagos mainland and island.",
                "price": 7500.00,
                "price_type": "hourly",
                "duration_minutes": 90,
                "location": {"address": "Surulere, Lagos", "city": "Surulere", "state": "Lagos"},
                "rating_average": 5.0,
                "rating_count": 42,
                "is_active": True,
            },
            {
                "provider_id": prov_id,
                "name": "Certified Inverter & Electrical Installations",
                "category": "Electrical",
                "description": "Licensed electrician for distribution boards, inverter and solar battery hookup, socket replacement, and surge protection.",
                "price": 8000.00,
                "price_type": "hourly",
                "duration_minutes": 60,
                "location": {"address": "Lekki Phase 1, Lagos", "city": "Lekki", "state": "Lagos"},
                "rating_average": 4.8,
                "rating_count": 35,
                "is_active": True,
            },
        ]

        for s in services:
            if s["name"] in existing_names:
                logger.info("Service [%s] already exists.", s["name"])
                continue
            try:
                client.from_("services").insert(s).execute()
                logger.info("Service [%s] created successfully.", s["name"])
            except Exception as e:
                logger.error("Service [%s] error: %s", s["name"], e)

    # 3. Verify counts
    cat_count = client.from_("categories").select("*", count="exact").execute().count
    srv_count = client.from_("services").select("*", count="exact").execute().count
    logger.info("Seeding complete! Categories in DB: %s, Services in DB: %s", cat_count, srv_count)


if __name__ == "__main__":
    main()
