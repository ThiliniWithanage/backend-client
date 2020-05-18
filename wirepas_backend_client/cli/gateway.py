"""
    Cli Module
    ===========

    Contains the cmd cli interface

    launch as wm-gw-cli or python -m wirepas_gateway_client.cli

    .. Copyright:
        Copyright 2019 Wirepas Ltd under Apache License, Version 2.0.
        See file LICENSE for full license details.
"""

import cmd
import json
import threading
from time import sleep, perf_counter

from .otap_status import OtapScratchPadStatus
from wirepas_messaging.gateway.api import GatewayState
from wirepas_messaging.gateway.api import GatewayResultCode

from wirepas_backend_client.cli.set_diagnostics.fea_set_neighbor_diagnostics import (
    SetDiagnostics,
    SetDiagnosticsIntervals,
)
from ..api.mqtt import MQTT_QOS_options
from ..mesh.sink import Sink
from ..mesh.gateway import Gateway

end_point_this_source: int = 255
end_point_default_diagnostic_control: int = 240
address_broadcast: int = 4294967295
cmd_MSAP_scratchpad_status: bytes = bytes.fromhex(
    "1900"
)  # MSAP scratchpad status


class GatewayCliCommands(cmd.Cmd):
    """
    GatewayCliCommands

    Implements a simple interactive cli to browse the network devices
    and send basic commands.
    """

    # pylint: disable=locally-disabled, no-member, too-many-arguments, unused-argument
    # pylint: disable=locally-disabled, too-many-boolean-expressions
    # pylint: disable=locally-disabled, invalid-name
    # pylint: disable=locally-disabled, too-many-function-args
    # pylint: disable=locally-disabled, too-many-public-methods

    def __init__(self, **kwargs):
        super().__init__()
        self.intro = (
            "Welcome to the Wirepas Gateway Client cli!\n"
            "Connecting to {mqtt_username}@{mqtt_hostname}:{mqtt_port}"
            " (unsecure: {mqtt_force_unsecure})\n\n"
            "You can now set all your command arguments using key=value!\n\n"
            "Type help or ? to list commands\n\n"
            "Type ! to escape shell commands\n"
            "Use Arrow Up/Down to navigate your command history\n"
            "Use TAB for auto complete\n"
            "Use CTRL-D, bye or q to exit\n"
        )

        self._prompt_base = "wm-gw-cli"
        self._prompt_format = "{} | {} > "
        self._display_pending_response = False
        self._display_pending_event = False
        self._display_pending_data = False
        self._selection = dict(sink=None, gateway=None, network=None)

        self._device_id_display_string_width = 16

        self._dataQueueMessageHandler = None
        self._eventQueueMessageHandler = None
        self._responseQueueMessageHandler = None

    @property
    def gateway(self) -> object:
        """
        Returns the currently selected gateway
        """
        return self._selection["gateway"]

    @property
    def sink(self) -> object:
        """
        Returns the currently selected sink
        """
        return self._selection["sink"]

    @property
    def network(self) -> object:
        """
        Returns the currently selected network
        """
        return self._selection["network"]

    def on_update_prompt(self):
        """ Updates the prompt with the gateway and sink selection """

        new_prompt = "{}".format(self._prompt_base)

        if self._selection["gateway"]:
            new_prompt = "{}:{}".format(
                new_prompt, self._selection["gateway"].device_id
            )

        if self._selection["sink"]:
            new_prompt = "{}:{}".format(
                new_prompt, self._selection["sink"].device_id
            )

        self.prompt = self._prompt_format.format(
            self.time_format(), new_prompt
        )

    def on_print(self, reply, reply_greeting=None, pretty=None):
        """ Prettified reply """
        serialization = reply
        try:
            serialization = reply.serialize()
            print(
                f"{self._reply_greeting} {serialization['gw_id']}"
                f"/{serialization['sink_id']}"
                f"/{serialization['network_id']}"
                f"|{serialization['source_endpoint']}"
                f"->{serialization['destination_endpoint']}"
                f"@{serialization['tx_time']}"
            )
        except AttributeError:
            serialization = None

        if self._minimal_prints:
            return

        indent = None
        if pretty or self._pretty_prints:
            indent = 4

        if serialization:
            print(json.dumps(serialization, indent=indent))
        else:
            print(reply)

    def set_message_handlers(
        self,
        dataQueueMessageHandler,
        eventQueueMessageHandler,
        responseQueueMessageHandler,
    ):
        """ Set message handlers that process incoming messages """
        self._dataQueueMessageHandler = dataQueueMessageHandler
        self._eventQueueMessageHandler = eventQueueMessageHandler
        self._responseQueueMessageHandler = responseQueueMessageHandler

    def _clear_message_handlers(self):
        """ Clear message handlers """
        self._dataQueueMessageHandler = None
        self._eventQueueMessageHandler = None
        self._responseQueueMessageHandler = None

    def on_response_queue_message(self, message):
        """ Method called when retrieving a message from the response queue """
        if self._responseQueueMessageHandler is not None:
            self._responseQueueMessageHandler(message)

        if self._display_pending_response:
            self.on_print(message, "Pending response message <<")

    def on_data_queue_message(self, message):
        """ Method called when retrieving a message from the data queue """
        if self._dataQueueMessageHandler is not None:
            self._dataQueueMessageHandler(message)

        if self._display_pending_data:
            self.on_print(message, "Pending data message <<")

    def on_event_queue_message(self, message):
        """ Method called when retrieving a message from the event queue """
        if self._eventQueueMessageHandler is not None:
            self._eventQueueMessageHandler(message)

        if self._display_pending_event:
            self.on_print(message, "Pending event message <<")

    def do_toggle_print_pending_responses(self, line):
        """
        When True prints any response that is going to be discarded
        """
        self._display_pending_response = not self._display_pending_response
        print(
            "display pending responses: {}".format(
                self._display_pending_response
            )
        )

    def do_toggle_print_pending_events(self, line):
        """
        When True prints any event that is going to be discarded
        """
        self._display_pending_event = not self._display_pending_event
        print("display pending events: {}".format(self._display_pending_event))

    def do_toggle_print_pending_data(self, line):
        """
        When True prints any data that is going to be discarded
        """
        self._display_pending_data = not self._display_pending_data
        print("display pending events: {}".format(self._display_pending_data))

    # track status
    def do_track_devices(self, line):
        """
        Displays the current selected devices for the desired amount of time.

        A key press will exit the loop.

        Usage:
            track_devices argument=value

        Arguments:
            - iterations=Inf
            - update_rate=1
            - silent=False

        Returns:
            Prints the current known devices
        """
        options = dict(
            iterations=dict(type=int, default=float("Inf")),
            update_rate=dict(type=int, default=1),
            silent=dict(type=bool, default=None),
        )

        args = self.retrieve_args(line, options)

        self._tracking_loop(self.do_list, **args)

    def do_track_data_packets(self, line):
        """
        Displays the incoming packets for one / all devices.

        A newline will exit the tracking loop

        Usage:
            track_data_packets argument=value

        Arguments:
            - gw_id=None
            - sink_id=None
            - network_id=None # filter by network
            - source_address=None
            - source_endpoint=None
            - destination_endpoint=None
            - iterations=Inf
            - update_rate=1 # period to print status if no message is acquired
            - show_events=False # will display answers as well
            - silent=False # when True the loop number is not printed

        Returns:
            Prints
        """

        options = dict(
            gw_id=dict(type=str, default=None),
            sink_id=dict(type=str, default=None),
            network_id=dict(type=int, default=None),
            source_address=dict(type=int, default=None),
            source_endpoint=dict(type=int, default=None),
            destination_endpoint=dict(type=int, default=None),
            iterations=dict(type=int, default=float("Inf")),
            update_rate=dict(type=int, default=1),
            show_events=dict(type=bool, default=False),
            silent=dict(type=bool, default=False),
        )

        args = self.retrieve_args(line, options)
        args["cli"] = self

        def handler_cb(cli, **kwargs):

            source_address = kwargs.get("source_address", None)
            source_endpoint = kwargs.get("source_endpoint", None)
            destination_endpoint = kwargs.get("destination_endpoint", None)
            network_id = kwargs.get("network_id", None)
            gw_id = kwargs.get("gw_id", None)
            sink_id = kwargs.get("sink_id", None)
            show_events = kwargs.get("show_events", None)

            def print_on_match(message):
                if (
                    cli.is_match(message, "gw_id", gw_id)
                    and cli.is_match(message, "sink_id", sink_id)
                    and cli.is_match(message, "network_id", network_id)
                    and cli.is_match(message, "source_address", source_address)
                    and cli.is_match(
                        message, "source_endpoint", source_endpoint
                    )
                    and cli.is_match(
                        message, "destination_endpoint", destination_endpoint
                    )
                ):
                    cli.on_print(message)

            for message in cli.consume_data_queue():
                print_on_match(message)

            if show_events:
                for message in cli.consume_event_queue():
                    print_on_match(message)

        self._tracking_loop(cb=handler_cb, **args)

        # commands

    def do_ls(self, line):
        """
        See list
        """
        self.do_list(line)

    def helpdo_list(self, line):
        """
        Lists all known networks and devices

        Usage:
            list

        Returns:
            Prints all known nodes
        """
        self.do_networks(line)

    def do_selection(self, line):
        """
        Displays the current selected devices

        Usage:
            selection

        Returns:
            Prints the currently selected sink, gateay and network
        """
        for k, v in self._selection.items():
            print("{} : {}".format(k, v))

    def _set_target(self):
        """ utility method to call when either the gateway or sink are undefined"""
        print("Please define your target gateway and sink")
        if self.gateway is None:
            self.do_set_gateway("")

        if self.sink is None:
            self.do_set_sink("")

    def _filter_nodes_by_gateway_id(
        self, nodes_list: object, gateway_id: object
    ) -> list:
        filtered_nodes_list = list()
        for node in nodes_list:
            if node.gateway_id == gateway_id:
                filtered_nodes_list.append(node)
        return filtered_nodes_list

    def _filter_sinks_by_gateway_id(
        self, sink_list: object, gateway_id: object
    ) -> list:
        filtered_sink_list = list()
        for sink in sink_list:
            if sink.gateway_id == gateway_id:
                filtered_sink_list.append(sink)
        return filtered_sink_list

    def _filter_nodes_by_sink_id(
        self, node_list: list, gateway_id: object
    ) -> list:
        filtered_node_list = list()
        for node in node_list:
            if node.device_id == gateway_id:
                filtered_node_list.append(node)
        return filtered_node_list

    def _sort_items_by_device_id(self, device_list: list):
        sorted_list = sorted(device_list, key=lambda item: item.device_id)
        return sorted_list

    def _get_gateway_configuration(self, gateway_device_id):
        ret = None
        message = self.mqtt_topics.request_message(
            "get_configs", **dict(gw_id=gateway_device_id)
        )
        self.request_queue.put(message)
        response = self.wait_for_answer(gateway_device_id, message)
        if response.res == GatewayResultCode.GW_RES_OK:
            ret = response.configs
        else:
            pass
        return ret

    def _filter_gateway_configuration(
        self, configs: dict, sink_id: str
    ) -> dict:
        # Parameter configs should be return value of _get_gateway_configuration
        ret = None
        for config in configs:
            if config["sink_id"] == sink_id:
                ret = config
                break
        return ret

    def _lookup_node_address(self, gateway_device_id, sink_id):
        ret = None
        gwConfig = self._get_gateway_configuration(gateway_device_id)
        if gwConfig is not None:
            sinkConfig = self._filter_gateway_configuration(gwConfig, sink_id)
            if sinkConfig is not None:
                ret = sinkConfig["node_address"]
        return ret

    def do_set_sink(self, line):
        """
        Sets the sink to use with the commands

        Usage:
            set_sink [Enter for default]

        Returns:
            Prompts the user for the sink to use when building
            network requests
        """

        if self.gateway is None:
            self.do_set_gateway(line)

        try:
            sinks = list(self.device_manager.sinks)

            if not sinks:
                self.do_gateway_configuration(line="")
                sinks = list(self.device_manager.sinks)
        except TypeError:
            sinks = list()

        current_gateway_id = self.gateway.device_id

        print("Current gateway is {}".format(current_gateway_id))

        filtered_sink_list = self._filter_sinks_by_gateway_id(
            sinks, current_gateway_id
        )

        filtered_sink_list = self._sort_items_by_device_id(filtered_sink_list)

        custom_index = len(filtered_sink_list)
        if filtered_sink_list:
            list(
                map(
                    lambda sink: print(
                        f"{filtered_sink_list.index(sink)} "
                        f":{sink.device_id}"
                        f" ( {sink.network_id} )"
                    ),
                    filtered_sink_list,
                )
            )
        print(f"{custom_index} : custom sink id")
        arg = input("Please enter your sink selection [0]: ") or 0
        try:
            arg = int(arg)
            self._selection["sink"] = filtered_sink_list[arg]

        except (ValueError, IndexError):
            arg = input("Please enter your custom sink id: ")
            self._selection["sink"] = Sink(device_id=arg)
            print(f"Sink set to: {self._selection['sink']}")

    def do_set_gateway(self, line):
        """
        Sets the gateways to use with the commands

        Usage:
            set_gateway [Enter for default]

        Returns:
            Prompts the user for the gateway to use when building
            network requests
        """
        try:
            gateways = list(self.device_manager.gateways)
        except TypeError:
            gateways = list()

        gateways = self._sort_items_by_device_id(gateways)
        online_gateways = self._filter_online_gateways(gateways)

        custom_index = len(online_gateways)
        print("Listing current online gateways:")
        if online_gateways:
            list(
                map(
                    lambda gw: print(
                        f"{online_gateways.index(gw)} " f":{gw.device_id}"
                    ),
                    online_gateways,
                )
            )

        print(f"{custom_index} : custom gateway id")
        arg = input("Please enter your gateway selection [0]: ") or 0
        try:
            arg = int(arg)
            self._selection["gateway"] = online_gateways[arg]

        except (ValueError, IndexError):
            arg = input("Please enter your custom gateway id: ")
            self._selection["gateway"] = Gateway(device_id=arg)
            print(f"Gateway set to: {self._selection['gateway']}")

        # Finally reset sink selection
        self._selection["sink"] = None

    def do_clear_offline_gateways(self, line):
        """
        Removes offline gateways from the remote broker.

        Usage:
            clear_offline_gateways
        """

        gateways = list(self.device_manager.gateways)
        for gateway in gateways:
            if gateway.state.value == GatewayState.OFFLINE.value:
                message = self.mqtt_topics.event_message(
                    "clear", **dict(gw_id=gateway.device_id)
                )
                message["data"].Clear()
                message["data"] = message["data"].SerializeToString()
                message["retain"] = True

                print("sending clear for gateway {}".format(message))

                # remove from state
                self.device_manager.remove(gateway.device_id)
                self.notify()

                self.request_queue.put(message)
                continue

    def do_sinks(self, line):
        """
        Displays the available sinks

        Usage:
            sinks

        Returns:
            Prints the discovered sinks
        """

        current_gateway = self._selection["gateway"]

        if current_gateway:
            # print sinks under gateway
            filtered_sink_list = self._filter_sinks_by_gateway_id(
                self.device_manager.sinks, current_gateway.device_id
            )

            sorted_sink_list = self._sort_items_by_device_id(
                filtered_sink_list
            )

            print(
                "Printing sinks of currently set gateway '{}'".format(
                    current_gateway.device_id
                )
            )

            sinks_str: str
            sinks_str = ""
            for sink in sorted_sink_list:
                sinks_str += "{} ".format(sink.device_id)
                print(sink)

            sinks_str = sinks_str[:-1]

            if len(sinks_str) > 0:
                gw_sinks = "( {} )".format(sinks_str)
                print(gw_sinks)
            else:
                print("No sinks!")
        else:
            sorted_sink_list = self._sort_items_by_device_id(
                self.device_manager.sinks
            )

            print("Printing sinks from all gateways..")

            print(
                "sink id".ljust(self._device_id_display_string_width),
                "( gateway )",
            )

            for sink in sorted_sink_list:
                print(
                    str(sink.device_id).ljust(
                        self._device_id_display_string_width
                    ),
                    "( {} )".format(sink.gateway_id),
                )

    def do_gateways(self, line):
        """
        Displays the available gateways

        Usage:
            gateways

        Returns:
            Prints the discovered gateways
        """
        print("Printing all encountered gateways of MQTT broker")

        print(
            "gateway id".ljust(self._device_id_display_string_width),
            "( sink0 .. sinkN )",
        )
        sorted_device_list = self._sort_items_by_device_id(
            self.device_manager.gateways
        )
        for gateway in sorted_device_list:
            sink_list = gateway.sinks

            sinks_str: str
            sinks_str = ""
            sorted_sink_list = self._sort_items_by_device_id(sink_list)
            for sink in sorted_sink_list:
                sinks_str += "{} ".format(sink.device_id)

            sinks_str = sinks_str[:-1]

            if len(sinks_str) > 0:
                print(
                    str(gateway.gateway_id).ljust(
                        self._device_id_display_string_width
                    ),
                    "(",
                    sinks_str,
                    ")",
                )
            else:
                print(
                    str(gateway.gateway_id).ljust(
                        self._device_id_display_string_width
                    )
                )

    def do_nodes(self, line):
        """
        Displays the available nodes

        Usage:
            nodes

        Returns:
            Prints the discovered nodes
        """
        current_gateway = self._selection["gateway"]

        if current_gateway:
            print(
                "Printing all encountered nodes of gateway {}".format(
                    current_gateway.device_id
                )
            )
            filtered_nodes_list: list = list()
            filtered_nodes_list = self._filter_nodes_by_gateway_id(
                self.device_manager.nodes, current_gateway.device_id
            )

            nodes_str: str
            nodes_str = ""
            sorted_filtered_nodes = sorted(
                list(filtered_nodes_list), key=lambda item: int(item.device_id)
            )
            for node in sorted_filtered_nodes:
                nodes_str += "{} ".format(node.device_id)

            nodes_str = nodes_str[:-1]

            if len(nodes_str) > 0:
                nodes_str = "( {} )".format(nodes_str)
                print(nodes_str)

            print("Total {} nodes".format(len(list(filtered_nodes_list))))

        else:
            print("Printing all encountered nodes of MQTT broker")

            nodes_str: str
            nodes_str = ""

            sorted_filtered_nodes = sorted(
                list(self.device_manager.nodes),
                key=lambda item: int(item.device_id),
            )

            for node in sorted_filtered_nodes:
                nodes_str += "{} ".format(node.device_id)

            nodes_str = nodes_str[:-1]

            if len(nodes_str) > 0:
                nodes_str = "( {} )".format(nodes_str)
                print(nodes_str)
            print(
                "Total {} nodes".format(len(list(self.device_manager.nodes)))
            )

    def do_networks(self, line):
        """
        Displays the available networks

        Usage:
            networks

        Returns:
            Prints the discovered networks
        """

        print("Printing all encountered networks of MQTT")

        sorted_networks = sorted(
            list(self.device_manager.networks),
            key=lambda item: int(item.network_id),
        )

        for network in sorted_networks:
            print(network.network_id)

    def do_gateway_configuration(self, line):
        """
        Acquires gateway configuration from the server and updates
        self.device_manager

        If no gateway is set, it will acquire configuration from all
        online gateways.

        When a gateway is selected, the configuration will only be
        requested for that particular gateway.

        Usage:
            gateway_configuration

        Returns:
            Current configuration for each gateway
        """

        ret = list()

        for gateway in self.device_manager.gateways:

            if gateway.state.value == GatewayState.OFFLINE.value:
                continue

            if self.gateway is not None:
                if self.gateway.device_id != gateway.device_id:
                    continue

            gw_id = gateway.device_id

            print("requesting configuration for {}".format(gw_id))
            configurations = self._get_gateway_configuration(gateway.device_id)

            for config in configurations:
                if config is not None:
                    ret.append(config)

            # Todo check what return type is needed and return needed item.

    def _refresh_device_manager(self):
        # refresh device manager (gw, sinks) by requesting configurations from
        # gateways

        for gateway in self.device_manager.gateways:

            if gateway.state.value == GatewayState.OFFLINE.value:
                continue

            if self.gateway is not None:
                if self.gateway.device_id != gateway.device_id:
                    continue

            gw_id = gateway.device_id

            config = self._get_gateway_configuration(gateway.device_id)
            self.device_manager.update(gw_id, config)

    def do_set_app_config(self, line):
        """
        Builds and sends an app config message

        Usage:
            set_app_config  argument=value

        Arguments:
            - sequence=1  # the sequence number - must be higher than the current one
            - data=001100 # payloady in hex string or plain string
            - interval=60 # a valid diagnostic interval (by default 60)

        Returns:
            Result of the request and app config currently set
        """
        options = dict(
            app_config_seq=dict(type=int, default=None),
            app_config_data=dict(type=int, default=None),
            app_config_diag=dict(type=int, default=60),
        )

        args = self.retrieve_args(line, options)

        if self.gateway and self.sink:
            # sink_id interval app_config_data seq

            gateway_id = self.gateway.device_id
            sink_id = self.sink.device_id

            if not self.is_valid(args) or not sink_id:
                self.do_help("set_app_config", args)

            message = self.mqtt_topics.request_message(
                "set_config",
                **dict(
                    sink_id=sink_id,
                    gw_id=gateway_id,
                    new_config={
                        "app_config_diag": args["app_config_diag"],
                        "app_config_data": args["app_config_data"],
                        "app_config_seq": args["app_config_seq"],
                    },
                ),
            )

            self.request_queue.put(message)
            self.wait_for_answer(gateway_id, message)

        else:
            self._set_target()
            self.do_set_app_config(line)

    @staticmethod
    def __print_scratch_pad_response(response) -> None:

        """
        UI spec
        Stored scratchpad:
        seq	    : 1
        len	    : 896
        crc 	: 58114
        status	: new
        type	: present

        Processed scratchpad:
        seq	    : 1
        crc	    : 36840
        len	    : 105072

        Firmware:
        area id : 259
        """
        text_field_len: int = 8
        sep_str: str = ": "
        hex_formatter_str: str = "02x"
        print("")

        print("Stored scratchpad:")
        print(
            "seq".ljust(text_field_len)
            + sep_str
            + "{}".format(response.stored_scratchpad["seq"])
        )
        print(
            "len".ljust(text_field_len)
            + sep_str
            + "{}".format(response.stored_scratchpad["len"])
        )
        print(
            "crc".ljust(text_field_len)
            + sep_str
            + "0x{}".format(
                format(
                    int(response.stored_scratchpad["crc"]), hex_formatter_str
                )
            )
        )
        print(
            "status".ljust(text_field_len)
            + sep_str
            + "{}".format(response.stored_status)
        )
        print(
            "type".ljust(text_field_len)
            + sep_str
            + "{}".format(response.stored_type)
        )
        print("")

        print("Processed scratchpad:")
        print(
            "seq".ljust(text_field_len)
            + sep_str
            + "{}".format(response.processed_scratchpad["seq"])
        )
        print(
            "len".ljust(text_field_len)
            + sep_str
            + "{}".format(response.processed_scratchpad["len"])
        )
        print(
            "crc".ljust(text_field_len)
            + sep_str
            + "0x{}".format(
                format(
                    int(response.processed_scratchpad["crc"]),
                    hex_formatter_str,
                )
            )
        )
        print("")

        print("Firmware:")
        print(
            "area id".ljust(text_field_len)
            + sep_str
            + "0x{}".format(
                format(int(response.firmware_area_id), hex_formatter_str)
            )
        )
        print("")

    def do_scratchpad_check(self, line) -> None:
        """
        Checks nodes scratchpad status connected to current selected sink

        Usage:
            scratchpad_check

        Returns:
            Prints status of nodes behind selected sink to console.
        """
        if self.gateway and self.sink:
            pass
        else:
            self._set_target()

        if self.gateway and self.sink:
            gateway_id = self.gateway.device_id
            sink_id = self.sink.device_id

            node_address = address_broadcast

            sink_node_address = self._lookup_node_address(gateway_id, sink_id)

            # Send scratchpad status to each nodes in network (use broadcast).
            broad_cast_message = self.create_scratchpad_status_query_msg(
                gateway_id, node_address, sink_id
            )
            sink_message = self.create_scratchpad_status_query_msg(
                gateway_id, sink_node_address, sink_id
            )

            self.request_queue.put(broad_cast_message)
            self.request_queue.put(sink_message)

            processed_scratchpads: dict = dict()
            stored_scratchpads: dict = dict()

            response_bcast = self.wait_for_answer(
                gateway_id, broad_cast_message
            )
            response_sink = self.wait_for_answer(gateway_id, sink_message)

            if response_bcast is not None and response_sink is not None:
                if (
                    response_bcast.res == GatewayResultCode.GW_RES_OK
                    and response_sink.res == GatewayResultCode.GW_RES_OK
                ):
                    silence_time_sec: int = 10
                    print(
                        "Commands OK. Collecting answers. Silence time "
                        "threshold is {} secs.".format(silence_time_sec)
                    )

                    # Collect responses. Collect responses until there is at
                    # silence_time_sec silence.

                    def __sc_handle_message(msg) -> bool:
                        ret = False
                        if (
                            msg.source_endpoint
                            == end_point_default_diagnostic_control
                        ):
                            otap_status: OtapScratchPadStatus = OtapScratchPadStatus(
                                msg.data_payload
                            )

                            if otap_status.is_valid():
                                node_just_size: int = 12
                                print(
                                    "Node "
                                    + "{}".format(
                                        hex(int(msg.source_address)).ljust(
                                            node_just_size
                                        )
                                    )
                                    + " processed scratchpad CRC:"
                                    + hex(
                                        int(otap_status.processedScratchPadCRC)
                                    )
                                    + " and seq:"
                                    + str(
                                        int.from_bytes(
                                            otap_status.processedScratchPadSeq,
                                            byteorder="little",
                                            signed=False,
                                        )
                                    )
                                    + " stored scratchpad CRC:"
                                    + hex(int(otap_status.storedScratchPadCRC))
                                    + " and seq:"
                                    + str(
                                        int.from_bytes(
                                            otap_status.storedScratchSeq,
                                            byteorder="little",
                                            signed=False,
                                        )
                                    )
                                )
                                processed_key = (
                                    "CRC:"
                                    + hex(
                                        int(otap_status.processedScratchPadCRC)
                                    )
                                    + " seq:"
                                    + str(
                                        int.from_bytes(
                                            otap_status.processedScratchPadSeq,
                                            byteorder="little",
                                            signed=False,
                                        )
                                    )
                                )

                                stored_key = (
                                    "CRC:"
                                    + hex(int(otap_status.storedScratchPadCRC))
                                    + " seq:"
                                    + str(
                                        int.from_bytes(
                                            otap_status.storedScratchSeq,
                                            byteorder="little",
                                            signed=False,
                                        )
                                    )
                                )

                                if processed_key not in processed_scratchpads:
                                    processed_scratchpads[processed_key] = 1
                                else:
                                    processed_scratchpads[processed_key] += 1

                                if stored_key not in stored_scratchpads:
                                    stored_scratchpads[stored_key] = 1
                                else:
                                    stored_scratchpads[stored_key] += 1
                                ret = True
                        return ret

                    last_msg_received_time = perf_counter()
                    while (
                        perf_counter() - last_msg_received_time
                        < silence_time_sec
                    ):
                        data_msg = self.get_message_from_data_queue()

                        if data_msg is not None:
                            if __sc_handle_message(data_msg) is True:
                                last_msg_received_time = perf_counter()

                        if data_msg is None:
                            default_sleep_time: float = 0.1
                            sleep(default_sleep_time)

                    def __print_dict_key_ratios(
                        itemsDict: dict, dictName: str
                    ) -> None:
                        key_len: int = 18
                        print("")
                        print("{} stats".format(dictName))
                        if len(itemsDict) > 0:
                            value_sum: int = 0
                            for val in itemsDict.values():
                                value_sum += val

                            for key in itemsDict:
                                print(
                                    "{}".ljust(key_len).format(key)
                                    + "{} node(s) ".ljust(4).format(
                                        itemsDict[key]
                                    )
                                    + "({}%)".format(
                                        int((itemsDict[key] / value_sum) * 100)
                                    )
                                )
                            print("Total {} nodes".format(value_sum))
                        else:
                            print("{} has no items")

                    # Calculate result
                    # Print result to console
                    __print_dict_key_ratios(processed_scratchpads, "Processed")
                    __print_dict_key_ratios(stored_scratchpads, "Stored")

                    print("")
                    print("--")

                    if len(stored_scratchpads) == 1:

                        first_key: str = list(stored_scratchpads.keys())[0]
                        print(
                            "All nodes of network has firmware '{}' stored.".format(
                                first_key
                            )
                        )
                        # Assume that this firmware is to be updated to all
                        # nodes
                        nodes_having_stored_scratch: int = stored_scratchpads[
                            first_key
                        ]
                        nodes_using_stored_scratch_pad: int = 0
                        if first_key in processed_scratchpads:
                            nodes_using_stored_scratch_pad = processed_scratchpads[
                                first_key
                            ]

                        print(
                            "Firmware '{}' is processed by {} node(s) ({}%).".format(
                                first_key,
                                nodes_using_stored_scratch_pad,
                                int(
                                    (
                                        nodes_using_stored_scratch_pad
                                        / nodes_having_stored_scratch
                                    )
                                    * 100
                                ),
                            )
                        )

                        if (
                            nodes_using_stored_scratch_pad
                            != nodes_having_stored_scratch
                            and nodes_having_stored_scratch > 0
                        ):
                            print("Network is ready for update command.")

                    else:
                        print(
                            "More than one firmware detected. "
                            "Network is distributing firmware."
                        )
                else:
                    print("Command FAIL [{}]".format(response_bcast.res))
            else:
                print("Command FAIL due timeout.")

        else:
            print("Command FAIL due invalid gw or sink selection.")
        print("")

    def create_scratchpad_status_query_msg(
        self, gateway_id, node_address, sink_id
    ):
        message = self.mqtt_topics.request_message(
            "send_data",
            **dict(
                sink_id=sink_id,
                gw_id=gateway_id,
                dest_add=node_address,
                src_ep=end_point_this_source,
                dst_ep=end_point_default_diagnostic_control,
                qos=1,
                payload=cmd_MSAP_scratchpad_status,
            ),
        )
        message["qos"] = MQTT_QOS_options.exactly_once.value
        return message

    def do_scratchpad_status(self, line) -> None:
        """
        Retrieves the scratchpad status from the sink

        Usage:
            scratchpad_status

        Returns:
            None
        """

        if self.gateway and self.sink:
            pass
        else:
            self._set_target()

        if self.gateway and self.sink:
            gateway_id = self.gateway.device_id
            sink_id = self.sink.device_id

            message = self.mqtt_topics.request_message(
                "otap_status", **dict(sink_id=sink_id, gw_id=gateway_id)
            )

            print("Performing upload. Request sent.")
            self.request_queue.put(message)

            print("Waiting response.")
            response = self.wait_for_answer(gateway_id, message)

            if response is not None:
                if response.res == GatewayResultCode.GW_RES_OK:
                    print("Command OK.")
                    self.__print_scratch_pad_response(response)
                else:
                    print("Command FAIL [{}]".format(response.res))
            else:
                print("Command FAIL due timeout.")
        else:
            print("Command FAIL due invalid gw or sink selection.")

    def do_scratchpad_update(self, line) -> None:
        """
        Sends a scratchpad update command to the sink

        Usage:
            scratchpad_update

        Returns:
            None
        """
        if self.gateway and self.sink:
            pass
        else:
            self._set_target()

        if self.gateway and self.sink:
            gateway_id = self.gateway.device_id
            sink_id = self.sink.device_id

            message = self.mqtt_topics.request_message(
                "otap_process_scratchpad",
                **dict(sink_id=sink_id, gw_id=gateway_id),
            )

            message["qos"] = MQTT_QOS_options.exactly_once.value

            print("Performing update. Request sent.")
            self.request_queue.put(message)
            scratchpad_update_timeout_sec: int = 60
            print(
                "Waiting response up to {} sec.".format(
                    scratchpad_update_timeout_sec
                )
            )

            response = self.wait_for_answer(
                gateway_id, message, scratchpad_update_timeout_sec
            )
            if response is not None:
                if response.res == GatewayResultCode.GW_RES_OK:
                    print("Command OK.")
                else:
                    print("Command FAIL [{}]".format(response.res))
            else:
                print("Command FAIL due timeout.")

        else:
            print("Command FAIL due invalid gw or sink selection.")

    def do_scratchpad_upload(self, line) -> None:
        """
        Uploads a scratchpad to the target sink/gateway pair

        Usage:
            scratchpad_upload filepath=<path/to/myscratchpad.otap>  sequence=<n>

        Arguments:
            - filepath=~/myscratchpad.otap # the path to the scratchpad
            - sequence=1 # the scratchpad sequence number

        Returns:
            None
        """

        file_path_arg_str: str = "filepath"
        sequence_arg_str: str = "sequence"
        scratchpad_load_cmd_str: str = "otap_load_scratchpad"

        options = dict(
            filepath=dict(type=str, default=None),
            sequence=dict(type=int, default=None),
        )
        args = self.retrieve_args(line, options)
        if (
            file_path_arg_str in args
            and sequence_arg_str in args
            and self.is_valid(args)
        ):

            if self.gateway and self.sink:
                pass
            else:
                self._set_target()

            if self.gateway and self.sink:

                gateway_id = self.gateway.device_id
                sink_id = self.sink.device_id

                try:
                    with open(args[file_path_arg_str], "rb") as f:
                        scratchpad = f.read()
                except FileNotFoundError:
                    print(
                        "File '{}' not found.".format(args[file_path_arg_str])
                    )
                    scratchpad = None

                if scratchpad is not None:
                    message = self.mqtt_topics.request_message(
                        scratchpad_load_cmd_str,
                        **dict(
                            sink_id=sink_id,
                            scratchpad=scratchpad,
                            seq=args[sequence_arg_str],
                            gw_id=gateway_id,
                        ),
                    )
                    message["qos"] = MQTT_QOS_options.exactly_once.value
                    print("Performing upload. Request sent.")
                    self.request_queue.put(message)

                    scratchpad_upload_timeout_sec: int = 60
                    print(
                        "Waiting response up to {} sec.".format(
                            scratchpad_upload_timeout_sec
                        )
                    )

                    response = self.wait_for_answer(
                        gateway_id,
                        message,
                        scratchpad_upload_timeout_sec,
                        True,
                    )
                    if response is not None:
                        if response.res == GatewayResultCode.GW_RES_OK:
                            print("Command OK.")
                        else:
                            print("Command FAIL [{}]".format(response.res))
                    else:
                        print("Command FAIL due timeout.")
                else:
                    print(
                        "Command FAIL. Cannot load scratchpad file from '{}'.".format(
                            args[file_path_arg_str]
                        )
                    )
            else:
                print("Command FAIL due invalid gw or sink selection.")
        else:
            print("Command FAIL. Not all expected arguments given.")
            self.do_help("scratchpad_upload", args)

    def _build_default_mqtt_request_options(self):
        options = dict(
            source_endpoint=dict(type=int, default=None),
            destination_endpoint=dict(type=int, default=None),
            destination_address=dict(type=int, default=None),
            payload=dict(type=self.strtobytes, default=None),
            timeout=dict(type=int, default=0),
            qos=dict(type=int, default=MQTT_QOS_options.exactly_once.value),
            is_unack_csma_ca=dict(type=bool, default=0),
            hop_limit=dict(type=int, default=0),
            initial_delay_ms=dict(type=int, default=0),
        )
        return options

    def do_send_data(self, line):
        """
        Sends a custom payload to the target address.

        Usage:
            send_data argument=value

        Arguments:
            - source_endpoint= 10 (default=None)
            - destination_endpoint=11   (default=None)
            - destination_address=101   (default=None)
            - payload=0011   (default=None)
            - timeout=0 # skip wait for a response (default=0)
            - qos=MQTT_QOS_options.exactly_once
            - is_unack_csma_ca=0  # if true only sent to CB-MAC nodes (default=0)
            - hop_limit=0  # maximum number of hops this message can do to reach its destination (<16) (default=0 - disabled)
            - initial_delay_ms=0 # initial delay to add to travel time (default: 0)

        Returns:
            Answer or timeout
        """

        options = self._build_default_mqtt_request_options()

        args = self.retrieve_args(line, options)

        if self.gateway and self.sink:

            if not self.is_valid(args):
                self.do_help("send_data", args)
            else:
                gateway_id = self.gateway.device_id
                sink_id = self.sink.device_id

                message = self.mqtt_topics.request_message(
                    "send_data",
                    **dict(
                        sink_id=sink_id,
                        dest_add=args["destination_address"],
                        src_ep=args["source_endpoint"],
                        dst_ep=args["destination_endpoint"],
                        payload=args["payload"],
                        qos=args["qos"],
                        is_unack_csma_ca=args["is_unack_csma_ca"],
                        hop_limit=args["hop_limit"],
                        initial_delay_ms=args["initial_delay_ms"],
                        gw_id=gateway_id,
                    ),
                )

                message["qos"] = MQTT_QOS_options.exactly_once.value
                self.request_queue.put(message)
                self.wait_for_answer(gateway_id, message)
        else:
            self._set_target()
            self.do_send_data(line)

    def send_message_to_mqtt_async(
        self,
        gatewayId,
        sinkId,
        nodeDestinationAddress,
        destinationEndPoint,
        sourceEndPoint,
        payload,
    ):

        # options = self._build_default_mqtt_request_options()

        message = self.mqtt_topics.request_message(
            "send_data",
            **dict(
                sink_id=sinkId,
                dest_add=nodeDestinationAddress,
                src_ep=sourceEndPoint,
                dst_ep=destinationEndPoint,
                payload=payload,
                qos=MQTT_QOS_options.exactly_once.value,
                is_unack_csma_ca=0,
                hop_limit=0,
                initial_delay_ms=0,
                gw_id=gatewayId,
            ),
        )

        message["qos"] = MQTT_QOS_options.exactly_once.value
        requestIdOfMessageToBeSent = message["data"].req_id

        dummyMode: bool = False  # If True, no messages is actually sent

        if dummyMode is True:
            print("Skipping sending ")
        else:
            self.request_queue.put(message)
        return requestIdOfMessageToBeSent

    def do_set_config(self, line):
        """
        Set a config on the target sink.

        Usage:
            set_config argument=value

        Arguments:
            - node_role=1 (int),
            - node_address=1003 (int),
            - network_address=100 (int),
            - network_channel=1 (int)
            - started=True (bool)

        Returns:
            Answer or timeout
        """
        options = dict(
            node_role=dict(type=int, default=None),
            node_address=dict(type=int, default=None),
            network_address=dict(type=int, default=None),
            network_channel=dict(type=int, default=None),
            started=dict(type=bool, default=None),
        )
        args = self.retrieve_args(line, options)

        if self.gateway and self.sink:
            gateway_id = self.gateway.device_id
            sink_id = self.sink.device_id

            new_config = {}
            for key, val in args:
                if val:
                    new_config[key] = val

            if not new_config:
                self.do_help("set_config", args)

            message = self.mqtt_topics.request_message(
                "set_config",
                **dict(
                    sink_id=sink_id, gw_id=gateway_id, new_config=new_config
                ),
            )
            self.request_queue.put(message)
            self.wait_for_answer(
                f"{gateway_id}/{sink_id}", message, timeout=self.timeout
            )

        else:
            self._set_target()

    def _getMenuOption(self, menuText: str, options: list):

        minValue = 0
        maxValue = len(options) - 1

        print(" ")
        print(menuText)

        menuId = 0
        for option in options:
            print("{}: {}".format(menuId, option))
            menuId += 1

        # print(menuText)
        inputStr = ""
        while (
            self._validateNumericInput(inputStr, minValue, maxValue) is False
        ):
            inputStr = input("[{}-{}]: ".format(minValue, maxValue)) or 0
        ret = options[int(inputStr)]
        return ret

    def _validateNumericInput(
        self, inputStr: str, minValue: int, maxValue: int
    ):
        ret: bool = False
        if inputStr.isnumeric():
            inputValue = int(inputStr)
            if minValue <= inputValue <= maxValue:
                ret = True

        return ret

    def _filter_online_gateways(self, gateways):
        online_gw_list: list = list()

        for gw in gateways:
            if str(gw.state) == "GatewayState.ONLINE":
                online_gw_list.append(gw)
        return online_gw_list

    def do_set_ndiag(self, line):
        """
        Enables or disables neighbor diagnostics on network level

        Usage:
            set_ndiag

            Application will ask parameters after invocation of command.

        Arguments asked from user:
            - Network ID
            - Diagnostic interval or off
            - Configuration for performing operation or cancel

        Returns:
            Prints progress on screen.
            There is a timeout.
        """

        print("Set neighbor diagnostics for network")
        print("")

        print("Refresh network list..")
        self._refresh_device_manager()
        sorted_networks = sorted(
            list(self.device_manager.networks),
            key=lambda item: int(item.network_id),
        )

        networkList: list
        networkList = list()
        for nw in sorted_networks:
            networkList.append(nw.network_id)

        if len(networkList) > 0:
            selectionID: int
            argNetworkId = self._getMenuOption(
                "Please enter network to be operated", networkList
            )

            argDiagnosticIntervalSec: SetDiagnosticsIntervals
            offOption = "off"
            diagnosticIntervalSelection = self._getMenuOption(
                "Select diagnostic interval(s) or off option",
                [
                    offOption,
                    SetDiagnosticsIntervals.i30.value,
                    SetDiagnosticsIntervals.i60.value,
                    SetDiagnosticsIntervals.i120.value,
                    SetDiagnosticsIntervals.i300.value,
                    SetDiagnosticsIntervals.i1200.value,
                ],
            )

            if diagnosticIntervalSelection == offOption:
                argDiagnosticIntervalSec = (
                    SetDiagnosticsIntervals.intervalOff.value
                )
            else:
                argDiagnosticIntervalSec = diagnosticIntervalSelection

            featureObject = SetDiagnostics(self.device_manager)

            if (
                featureObject.setArguments(
                    int(argNetworkId), argDiagnosticIntervalSec
                )
                is True
            ):

                targetSinks = featureObject.getSinksBelongingToNetwork(
                    int(argNetworkId)
                )

                sinksAddressInfo = dict()

                sinkAddressCheckOk: bool = True
                sinkAddressCheckOk = self.createSinkAddressInfo(
                    sinkAddressCheckOk, sinksAddressInfo, targetSinks
                )

                if sinkAddressCheckOk is True:
                    print(" ")
                    print("About to send messages to:")
                    targetList = ""
                    for gw in targetSinks:
                        for sink in targetSinks[gw]:
                            targetList += "{}/{}:{}  ".format(
                                gw, sink, sinksAddressInfo[gw][sink]
                            )
                    print(targetList)

                    uiCommandOptionProceedYes = "yes"
                    uiCommandOptionoptionProceedNo = "no"

                    proceed = self._getMenuOption(
                        "Args good. Proceed?",
                        [
                            uiCommandOptionoptionProceedNo,
                            uiCommandOptionProceedYes,
                        ],
                    )

                    if proceed == uiCommandOptionProceedYes:
                        featureObject.setMQTTmessageSendFunction(
                            self.send_message_to_mqtt_async
                        )

                        featureObject.setSinksAddressInfo(sinksAddressInfo)

                        self.set_message_handlers(
                            featureObject.onDataQueueMessage,
                            featureObject.onEventQueueMessage,
                            featureObject.onResponseQueueMessage,
                        )

                        exitWorkers = False

                        def applicationLoopWorker():
                            featureObject.performOperation()
                            nonlocal exitWorkers
                            exitWorkers = True

                        def backEndClientMessageLoopWorker():
                            # Todo Move this to upper level.
                            # Now here because not knowing the
                            # impacts yet.
                            nonlocal exitWorkers
                            while exitWorkers is False:
                                msgReceived = False
                                msg = self.get_message_from_response_queue()
                                if msg is not None:
                                    msgReceived = True
                                    self.on_response_queue_message(msg)

                                msg = self.get_message_from_data_queue()
                                if msg is not None:
                                    msgReceived = True
                                    self.on_data_queue_message(msg)

                                msg = self.get_message_from_event_queue()
                                if msg is not None:
                                    msgReceived = True
                                    self.on_event_queue_message(msg)

                                if msgReceived is False:
                                    defaultSleepSecs = 0.05
                                    sleep(defaultSleepSecs)

                        t1 = threading.Thread(target=applicationLoopWorker)
                        t2 = threading.Thread(
                            target=backEndClientMessageLoopWorker
                        )
                        t1.start()
                        t2.start()

                        # Work start tha this remains blocked until threads
                        # complete

                        t1.join()
                        t2.join()
                        self._clear_message_handlers()
                    else:
                        print(" ")
                        print("Aborted!")
                else:
                    print(" ")
                    print("Sink node address lookup failed!")
            else:
                print(" ")
                print("Arguments not valid!")
        else:
            print(" ")
            print("No networks available!")

    def createSinkAddressInfo(
        self, sinkAddressCheckOk, sinksAddressInfo, targetSinks
    ):
        for gw in targetSinks:
            for sink in targetSinks[gw]:
                sinkNodeAddress = self._lookup_node_address(gw, sink)
                if sinkNodeAddress is not None:
                    if gw not in sinksAddressInfo:
                        sinksAddressInfo[gw] = dict()

                    if sink not in sinksAddressInfo[gw]:
                        sinksAddressInfo[gw][sink] = dict()

                    sinksAddressInfo[gw][sink] = sinkNodeAddress
                else:
                    sinkAddressCheckOk = False
                    break
        return sinkAddressCheckOk