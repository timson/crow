import asyncio
import crow_security
import pprint
import config

email = config.email
password = config.password
mac = config.mac

p = pprint.PrettyPrinter(indent=4)

async def main():
    async with crow_security.Session(email, password) as session:
        panel = await session.get_panel(mac)
        print("Panel: ")
        p.pprint(panel)
        #areas = await panel.get_areas()
        #p.pprint(areas)

        async def on_message(msg):
            print("Message reveived:")
            p.pprint(msg)

        await session.ws_connect(panel.id, on_message)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
