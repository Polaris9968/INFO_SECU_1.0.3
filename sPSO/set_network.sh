#!/bin/bash

INTERFACE="lo"

case "$1" in
    "10G")
        tc qdisc del dev $INTERFACE root 2>/dev/null
        tc qdisc add dev $INTERFACE root netem delay 0.1ms
        echo "Set latency to 0.1ms (no bandwidth limit, assuming 10Gbps+ link)"
        ;;

    "1G")
        tc qdisc del dev $INTERFACE root 2>/dev/null
        tc qdisc add dev $INTERFACE root handle 1: htb default 10
        tc class add dev $INTERFACE parent 1: classid 1:10 htb rate 100mbit ceil 1Gbit
        tc qdisc add dev $INTERFACE parent 1:10 handle 10: netem delay 50ms
        echo "Set network bandwidth to 1Gbps and RTT latency to 50ms"
        ;;

    "100M")
        tc qdisc del dev $INTERFACE root 2>/dev/null
        tc qdisc add dev $INTERFACE root handle 1: htb default 10
        tc class add dev $INTERFACE parent 1: classid 1:10 htb rate 100mbit ceil 100mbit
        tc qdisc add dev $INTERFACE parent 1:10 handle 10: netem delay 50ms
        echo "Set network bandwidth to 100Mbps and RTT latency to 50ms"
        ;;

    "10M")
        tc qdisc del dev $INTERFACE root 2>/dev/null
        tc qdisc add dev $INTERFACE root handle 1: htb default 10
        tc class add dev $INTERFACE parent 1: classid 1:10 htb rate 10mbit ceil 10mbit
        tc qdisc add dev $INTERFACE parent 1:10 handle 10: netem delay 50ms
        echo "Set network bandwidth to 10Mbps and RTT latency to 50ms"
        ;;

    "clean")
        tc qdisc del dev $INTERFACE root 2>/dev/null
        echo "Cleaned all network limitations"
        ;;

    "show")
        echo "Qdisc info:"
        tc qdisc show dev $INTERFACE
        echo ""
        echo "Class info:"
        tc class show dev $INTERFACE
        echo ""
        echo "Filter info:"
        tc filter show dev $INTERFACE
        ;;

    *)
        echo "Usage: sudo $0 {10G|1G|100M|10M|clean|show}"
        echo ""
        echo "Note:"
        echo "  10G  -> 0.1ms latency (no real bandwidth limit)"
        echo "  1G  -> 1Gbps bandwidth, 50ms RTT"
        echo "  100M -> 100Mbps bandwidth, 50ms RTT"
        echo "  10M  -> 10Mbps bandwidth, 50ms RTT"
        echo "  clean-> Remove all tc settings"
        echo "  show -> Display tc qdisc/class/filter info"
        ;;
esac
