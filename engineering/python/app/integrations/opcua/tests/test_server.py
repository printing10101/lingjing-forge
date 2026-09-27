"""Minimal OPC UA test server for integration testing.

Exposes four variables matching the adapter's expected node names:
  - SpindleSpeed (float)
  - SpindleLoad (float)
  - FeedRate (float)
  - Execution (string)
"""

import asyncio
import random
import logging

from asyncua import Server


async def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://0.0.0.0:4840/freeopcua/server/")
    server.set_server_name("OPC UA Test Server")

    # Register namespace
    uri = "http://example.org/opcua/test/"
    idx = await server.register_namespace(uri)

    # Create objects node
    objects = server.nodes.objects
    myobj = await objects.add_object(idx, "CNCMachine")

    # Add variables matching adapter's expected node names
    speed_node = await myobj.add_variable(idx, "SpindleSpeed", 0.0)
    load_node = await myobj.add_variable(idx, "SpindleLoad", 0.0)
    feed_node = await myobj.add_variable(idx, "FeedRate", 0.0)
    exec_node = await myobj.add_variable(idx, "Execution", "IDLE")

    await server.start()
    logger.info("OPC UA test server running at opc.tcp://localhost:4840/freeopcua/server/")
    logger.info("Press Ctrl+C to stop")

    try:
        # Simulate changing values every second
        cycle = 0
        while True:
            await asyncio.sleep(1.0)
            cycle += 1
            await speed_node.write_value(random.uniform(1000, 5000))
            await load_node.write_value(random.uniform(10, 90))
            await feed_node.write_value(random.uniform(100, 2000))
            await exec_node.write_value(random.choice(["ACTIVE", "IDLE", "PROGRAM"]))
            logger.info(
                f"  cycle {cycle}: speed={await speed_node.read_value():.1f}, "
                f"load={await load_node.read_value():.1f}, "
                f"feed={await feed_node.read_value():.1f}, "
                f"exec={await exec_node.read_value()}"
            )
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
