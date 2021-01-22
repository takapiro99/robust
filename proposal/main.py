import sys
# import threading
import utils
# from .scu import SCU
from myscu import NewSCU
# from gpiozero import LED


def main():
    if sys.argv[1] == "sender":
        # scu = SCU(mtu=1500)
        myscu = NewSCU(mtu=1500)
        # scu.bind_as_sender(receiver_address=("169.254.229.153", 8888))
        myscu.bind_as_sender(receiver_address=("localhost", 8888))
        myscu.send("./proposal/data/data0", 0)
        print("file0 sent!")
        # try:
        #     for id in range(0, 1000):
        #         myscu.send(f"./data/data{id}", id)
        #         # After sending file, change to receiver to recevier IDs. 
        #         print(f"file sent: {id}", end="\r")
        # except Exception as e:
        #     print(e)
        #     myscu.drop() # just in case

    elif sys.argv[1] == "receiver":
        myscu = NewSCU(mtu = 1500)
        # scu.bind_as_receiver(receiver_address = ("169.254.155.219", 8888))       
        myscu.bind_as_receiver(receiver_address = ("localhost", 8888))
        for i in range(0, 1000):
            filedata = myscu.recv()
            utils.write_file(f"./data/data{i}", filedata)
            print(f"file received: {i}")

if __name__ == '__main__':
    main()

# think_outside_the_box = True
# if think_outside_the_box:
#     gpio_thread = threading.Thread(
#         target=yis)
#     gpio_thread.setDaemon(True)
#     gpio_thread.start()


# def yis():
#     led = LED(17)
#     while True:
#         #echo 0 > /sys/class/gpio/gpio17/value

#         led.off()