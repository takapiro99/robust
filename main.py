import sys
# import threading
import utils
from scu import SCU

def main():
    if sys.argv[1] == "sender":
        scu = SCU(mtu=1500)
        # scu.bind_as_sender(receiver_address=("169.254.229.153", 8888))
        scu.bind_as_sender(receiver_address=("localhost", 8888))
        try:
            # serial
            # first just try with data0!
            scu.send("./data/data0", 0)
            print(f"file sent: 0")
            # for id in range(0, 1000):
            #     scu.send(f"./data/data{id}", id)
            #     print(f"file sent: {id}", end="\r")
        except Exception as e:
            print(e)
            scu.drop() # just in case

    elif sys.argv[1] == "receiver":
        scu = SCU(mtu = 1500)
        # scu.bind_as_receiver(receiver_address = ("169.254.155.219", 8888))
        scu.bind_as_receiver(receiver_address = ("localhost", 8888))
        for i in range(0, 1000):
            filedata = scu.recv()
            utils.write_file(f"./data/data{i}", filedata)
            print(f"file received: {i}", end="\r")

if __name__ == '__main__':
    main()
