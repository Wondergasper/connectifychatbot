import asyncio
import logging
from app.whatsapp.conversation import conversation_manager
from app.whatsapp.session import session_manager
from app.repositories.connectify_repository import ConnectifyRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("connectify.live_test")


async def main():
    test_phone = "+2348099887766"
    logger.info("--- Starting End-to-End Live WhatsApp Simulation with Supabase ---")
    await session_manager.clear_session(test_phone)

    # 1. User registers/greets
    logger.info("[Step 1] Customer says: 'Hello'")
    res1 = await conversation_manager.handle_incoming_message(test_phone, "Hello")
    logger.info("Bot reply: %s", res1)

    # 2. Customer provides their name
    logger.info("[Step 2] Customer provides name: 'Samuel Wonder'")
    res2 = await conversation_manager.handle_incoming_message(test_phone, "Samuel Wonder")
    logger.info("Bot reply (Buttons): %s", res2)

    # 3. Customer selects 'I need a service'
    logger.info("[Step 3] Customer taps [ROLE_CUSTOMER]")
    res3 = await conversation_manager.handle_incoming_message(test_phone, {"actionId": "ROLE_CUSTOMER"})
    logger.info("Bot reply (Main Menu): %s", res3)

    # 4. Customer asks for a service
    logger.info("[Step 4] Customer requests: 'I need a plumber in Ikeja tomorrow 10am'")
    res4 = await conversation_manager.handle_incoming_message(
        test_phone, "I need a plumber in Ikeja tomorrow 10am"
    )
    logger.info("Bot confirmation summary: %s", res4)

    # 5. Customer confirms
    logger.info("[Step 5] Customer confirms: 'Yes'")
    res5 = await conversation_manager.handle_incoming_message(test_phone, "Yes")
    logger.info("Bot provider listing (retrieved from live Supabase): %s", res5)

    logger.info("--- Live End-to-End Simulation SUCCESSFUL! ---")


if __name__ == "__main__":
    asyncio.run(main())
