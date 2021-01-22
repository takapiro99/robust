from queue import Queue
import socket
import threading
from enum import Enum
import random
import time

from packet import SCUPacketType, SCUHeader, SCUPacket
import utils

n = 10

class SCUMode(Enum):
    SendMode = 0
    RecvMode = 1

class RecvMode(Enum):
    WaitNewFileUntilDataEndComes = 0
    SendMissingSeqsUntilAnyResponseComes = 1
    RecvUntilEndComes = 2
    RecvUntilFileCompletes = 3  # missing が n 個以下になったら発動することにする
    SendFinUntilNextFileComes = 4


class SendMode(Enum):
    SendNewFile = 0
    KeepSendingDataEndUntilResendReqComes = 1
    SendMissingSeqs = 2
    KeepSendingEndUntilResendReqComes = 3
    SendingMissingSeqLoop = 4  # missing が n 個以下になったら発動。fin来たら次へ。


class NewSCU:
    def __init__(self, mtu=1500):
        self.mtu = mtu
        self.current_fileno = 0
        self.missing_seqs_str = ""

    def bind_as_sender(self, receiver_address):
        self.mode = SCUMode.SendMode
        self.send_mode = SendMode.SendNewFile
        self.connection_manager = {}
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(receiver_address)
        self.receiver_address = receiver_address
        self.lock = threading.Lock()

        sender_packet_loop_thread = threading.Thread(target=self._sender_packet_loop)
        sender_packet_loop_thread.setDaemon(True)
        sender_packet_loop_thread.start()

    def bind_as_receiver(self, receiver_address):
        self.mode = SCUMode.RecvMode
        self.received_files_data = {}
        self.receive_mode = RecvMode.WaitNewFileUntilDataEndComes
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(receiver_address)
        self.receiver_address = receiver_address
        self.file_received = Queue()
        self.task_manager = Queue()

        receiver_packet_loop_thread = threading.Thread(target=self._receiver_packet_loop)
        receiver_packet_loop_thread.setDaemon(True)
        receiver_packet_loop_thread.start()
        recv_controller = threading.Thread(target=self._receiver_controller)
        recv_controller.setDaemon(True)
        recv_controller.start()
        # recv_controller と receiver_packet_loop の二つを回す

    def drop(self):
        if self.mode == SCUMode.SendMode:
            self.connection_manager.clear()
            self.socket.close()

    def _sender_packet_loop(self):
        print("jaldsjflaskdj")
        # if self.mode == SCUMode.RecvMode:
        #     raise Exception
        while True:
            try:
                packet = SCUPacket()
                packet.from_raw(self.socket.recv(2048))
                print(packet)
                # psuedo packet loss
                # if random.random() >= 0.5:
                #     continue
                #     if packet.header.id not in self.connection_manager:
                #         print("not the file")
                #         continue  # そのfileじゃないとき
                if packet.header.typ == SCUPacketType.Fin.value:
                    self.connection_manager[packet.header.id].put((True, packet.header.seq))
                elif packet.header.typ == SCUPacketType.Rtr.value:
                    self.connection_manager[packet.header.id].put((False, packet.header.seq))
            except Exception as e:  # recvが失敗した時とputが失敗した時は(適当)
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()
            # else:
            # print('hasjdofasjd')


    def send(self, filepath, fileno):  # will lock the thread
        if self.mode == SCUMode.RecvMode:
            raise Exception
        queue = Queue()
        print(type(fileno))
        self.connection_manager[fileno] = queue  # register queue
        data_fragments = utils.split_file_into_mtu(filepath, self.mtu)
        all_packets = []
        current_resendID = 0
        for (seq, df) in enumerate(data_fragments):
            header = SCUHeader()
            if seq == len(data_fragments) - 1:
                header.from_dict({"typ": SCUPacketType.DataEnd.value, "id": fileno, "seq": seq, "resendID": 0})
            else:
                header.from_dict({"typ": SCUPacketType.Data.value, "id": fileno, "seq": seq, "resendID": 0})
            packet = SCUPacket()
            packet.from_dict({"header": header, "payload": df})
            all_packets.append(packet)
        print(len(all_packets))

        while True:  # main loop
            # print(self.send_mode)
            if self.send_mode == SendMode.SendNewFile:
                # first of all send all data "once"
                for seq in range(len(all_packets)):
                    with self.lock:  # lock
                        self.socket.sendto(all_packets[seq].raw(), self.receiver_address)
                        # print(self.receiver_address)
                self.send_mode = SendMode.KeepSendingDataEndUntilResendReqComes
            elif self.send_mode == SendMode.KeepSendingDataEndUntilResendReqComes:
                dataEnd = all_packets[-1]
                #dataEnd.header.resendID = current_resendID
                dataEnd.header.resendID = 0
                # while True:
                with self.lock:
                    self.socket.sendto(dataEnd.raw(), self.receiver_address)
                try:
                    packet = queue.get(block=False)
                except Exception as e:
                    if e == KeyboardInterrupt:
                        raise KeyboardInterrupt
                    else:
                        print("No packet in queue")
                        pass
                else:
                    print('hji')
                    if packet.header.typ == SCUPacketType.Rtr and packet.header.resendID >= 1:
                        current_resendID = packet.header.resendID
                        self.missing_seqs_str = packet.payload
                        print("届いたよ")
                        self.send_mode = SendMode.SendMissingSeqs
            elif self.send_mode == SendMode.SendMissingSeqs:
                missing_seqs = map(int, self.missing_seqs_str.split(","))
                # 言われてた欠損ファイルを一回送る、それか二周送る
                for i in range(len(missing_seqs) - 1):
                    data = all_packets[fileno][missing_seqs[i]]
                    data.header.resendID = current_resendID
                    with self.lock:  # lock
                        self.socket.sendto(data.raw(), self.receiver_address)
                end_packet = all_packets[fileno][missing_seqs[-1]]
                end_packet.header.resendID = current_resendID
                end_packet.header.typ = SCUPacketType.End
                self.end_packet = end_packet
                self.send_mode = SendMode.KeepSendingEndUntilResendReqComes
                print('switching mode...')
            elif self.send_mode == SendMode.KeepSendingEndUntilResendReqComes:
                while True:
                    # 送る
                    with self.lock:  # lock
                        self.socket.sendto(self.end_packet.raw(), self.receiver_address)
                        print('sending...', self.end_packet)
                    # 新しいresend requestが来る
                    try:
                        packet = self.task_manager.get(block=False)
                    except Exception as e:  # queue is empty
                        if e == KeyboardInterrupt:
                            raise KeyboardInterrupt
                        else:
                            pass
                    else:  # when any incoming packet
                        if packet.header.typ == SCUPacketType.Rtr and packet.header.resendID > current_resendID:
                            current_resendID = packet.header.resendID
                            self.missing_seqs_str = packet.payload
                            if len(packet.payload.split(',')) <= 10:
                                self.send_mode = SendMode.SendingMissingSeqLoop
                            else:
                                self.send_mode = SendMode.SendMissingSeqs
            elif self.send_mode == SendMode.SendingMissingSeqLoop:
                # send missing thing loop 
                # with self.lock:  # lock
                    # self.socket.sendto(self.None.raw(), self.receiver_address)
                try:
                    packet = self.task_manager.get(block=False)
                except Exception as e:  # queue is empty
                    if e == KeyboardInterrupt:
                        raise KeyboardInterrupt
                    else:
                        pass
                else:  
                    if packet.header.typ == SCUPacketType.Fin and packet.header.id == fileno:
                        del(self.connection_manager[id]) # コネクションを解除
                        return
                pass
            else:
                raise Exception

    def _receiver_controller(self):
        def store_data(key, seq, payload):
            self.received_files_data[key][seq] = payload
        initial_resendID = 1
        received_files_flag = {}
        file_lengths = {}
        resend_id_count = {}
        while True:  # main loop
            if self.receive_mode == RecvMode.WaitNewFileUntilDataEndComes:
                try:
                    while True:
                        try:
                            packet = self.task_manager.get(block=False)
                            if self.current_fileno != packet.header.id:
                                print('asdad')
                                continue # ignore wrong file
                            if packet.header.resendID != 0:
                                print('asd')
                                continue
                        except Exception as e:  # when queue is empty
                            if e == KeyboardInterrupt:
                                raise KeyboardInterrupt
                            else:
                                break
                        else:
                            key = packet.header.id
                            if key not in self.received_files_data:
                                self.received_files_data[0] = [b""]*200
                                received_files_flag[0] = False
                            if packet.header.typ == SCUPacketType.DataEnd.value or packet.header.typ == SCUPacketType.Data.value:
                                store_data(key, packet.header.seq, packet.payload)
                                if packet.header.typ == SCUPacketType.DataEnd.value:
                                    if key not in file_lengths:
                                        file_lengths[key] = packet.header.seq + 1
                                        resend_id_count[key] = initial_resendID
                                        # print(self.received_files_data[0])
                                        unreceived_seqs_str, missing_seqs_count = self.calculate_rtr(key, packet.header.seq)
                                        print(f"file:{key}, missing: {unreceived_seqs_str}")
                                        self.missing_seqs_str = unreceived_seqs_str
                                        self.receive_mode = RecvMode.SendMissingSeqsUntilAnyResponseComes
                                        break
                            else:
                                print("non data typ packet came here", self.packet_info(packet))
                except Exception as e:  # sendtoが失敗した時は(適当)
                    if e == KeyboardInterrupt:
                        raise KeyboardInterrupt
                    else:
                        import traceback
                        traceback.print_exc()
            # SendMissingSeqsUntilAnyResponseComes with corresponding resendID
            elif self.receive_mode == RecvMode.SendMissingSeqsUntilAnyResponseComes:
                # 再送要求を送る
                # TODO: 若干待つ？
                # self.response(SCUPacketType.Rtr.value, self.receiver_address, self.current_fileno, 0, resend_id_count[self.current_fileno], self.missing_seqs_str)
                self.socket.sendto(b"fa;wkemfa;woeka;owken;avowken;aojwdn;aowkdfj;aowiehfaiuwerhgfaliwer", self.receiver_address)
                try:
                    packet = self.task_manager.get(block=False)
                except Exception as e:  # queue is empty
                    if e == KeyboardInterrupt:
                        raise KeyboardInterrupt
                    # else:
                    #     pass
                    # else:
                    #     key = packet.header.id
                    # # 求めているresendidのパケットが来たら次のモードへ。
                    # if self.current_fileno == key and packet.header.resendID == resend_id_count[key]:
                    #     print(resend_id_count[key])
                    #     store_data(key, packet.header.seq, packet.payload)
                    #     self.receive_mode = RecvMode.RecvUntilEndComes
                    #     print(self.receive_mode)
                    # else:
                    #     continue
            elif self.receive_mode == RecvMode.RecvUntilEndComes:
                while True:
                    try:
                        packet = self.task_manager.get(block=False)
                    except Exception as e:  # queue is empty
                        if e == KeyboardInterrupt:
                            raise KeyboardInterrupt
                        else:
                            pass
                    else:  # when any incoming packet
                        key = packet.header.id
                        store_data(packet.header.id, packet.header.seq, packet.payload)
                        if packet.header.typ == SCUPacketType.End:
                            print("end came!")
                            # update resend_id
                            if resend_id_count[key] == 255:
                                resend_id_count[key] = 1
                            else:
                                resend_id_count[key] += 1
                            unreceived_seqs_str, missing_seqs_count = self.calculate_rtr(packet.header.id, packet.header.seq)
                            print(unreceived_seqs_str)
                            self.missing_seqs_str = unreceived_seqs_str
                            if missing_seqs_count <= n:
                                self.receive_mode = RecvMode.RecvUntilFileCompletes
                            else:
                                self.receive_mode = RecvMode.SendMissingSeqsUntilAnyResponseComes
            elif self.receive_mode == RecvMode.RecvUntilFileCompletes:
                try:
                    pass
                except Exception as e:
                    pass
                else:
                    pass
                finally:
                    # パケットが来るごとに全部出来上がったかを確認し、
                    # 完成したら SendFinUntilNextFileComes に変える。
                    pass
                    #  check
                # そしてこれ
                # self.file_received.put((key, received_files_length[key]))
            elif self.receive_mode == RecvMode.SendFinUntilNextFileComes:
                self.response(SCUPacketType.Fin.value, self.receiver_address, self.current_fileno, 0, 0, None)
                # TODO: ちょっと待つ？
                try:
                    packet = self.task_manager.get(block=False)
                    if self.current_fileno + 1 != packet.header.id:
                        continue
                except Exception as e:  # queue is empty
                    if e == KeyboardInterrupt:
                        raise KeyboardInterrupt
                    else:
                        break
                else:  # 新しいファイル来たー
                    key = packet.header.id
                    if key not in self.received_files_data:
                        # 新規登録。記念すべき1seq目
                        self.received_files_data[key] = [b""]*200
                        store_data(key, packet.header.seq, packet.payload)
                        received_files_flag[key] = False
                        self.current_fileno = key
                        self.missing_seqs_str = ""
                        self.receive_mode = RecvMode.WaitNewFileUntilDataEndComes
            else:
                pass

    def packet_info(packet):
        msg = f"file: {packet.header.id}, seq: {packet.header.seq}, typ: {packet.header.typ}"
        return msg

    # receives packet, unpack it
    # adds (packet, from_addr) to queue (task_manager)
    def _receiver_packet_loop(self):
        if self.mode == SCUMode.SendMode:
            raise Exception
        while True:
            try:
                data, from_addr = self.socket.recvfrom(2048)
                # TODO: remove this
                # psuedo packet loss
                if random.random() >= 0.5:
                    continue
                packet = SCUPacket()
                packet.from_raw(data)
                # print(packet.header.resendID)
                # putting it to task_manager Queue() (<Packet>, from_addr)
                self.task_manager.put(packet)
            except Exception as e:  # when recv fails
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()

    def calculate_rtr(self, key, seq):
        """
        returns (string of missing seqs joined with ",", count of missing seqs)
        """
        unreceived_seqs = []
        for sq in range(0, seq):
            if not self.received_files_data[key][sq]:
                unreceived_seqs.append(str(sq))
        return (",".join(unreceived_seqs), len(unreceived_seqs))

    def is_all_received(self, key, length):
        """
        checks if all seqs are received with given key and file(seq) length
        """
        for i in range(0, length):
            if not self.received_files_data[key][i]:
                return False
        return True

    def response(self, typ, addr, key, seq, resendID, content=b""):
        """
        responses a single packet of retry, or fin.

        it can be used to just send packets.
        """
        # print(typ)
        if self.mode == SCUMode.SendMode:
            raise Exception
        if typ == SCUPacketType.Rtr.value:
            header = SCUHeader()
            header.from_dict({"typ": typ, "id": key, "seq": seq, "resendID": resendID})
            packet = SCUPacket()
            packet.from_dict({"header": header, "payload": content.encode() })
            # print("sending...") # working!
            # self.socket.sendto(packet.raw(), addr)
            self.socket.sendto(b"fa;woeaf;wokemfa;woeka;owken;avowken;aojwdn;aowkdfj;aowiehfaiuwerhgfaliwer", addr)

        elif typ == SCUPacketType.Fin.value:
            header = SCUHeader()
            header.from_dict({"typ": typ, "id": key, "seq": seq, "resendID": resendID})
            packet = SCUPacket()
            packet.from_dict({"header": header, "payload": b'' })
            self.socket.sendto(packet.raw(), addr)
        else:
            print("Attempting  to pass")
            pass
            # print("unable to replu")

    def recv(self):
        if self.mode == SCUMode.SendMode:
            raise Exception
        key, length = self.file_received.get()
        return utils.fold_data(self.received_files_data[key], length)


"""

1. Taro attempts to send file: 123456789
2. Hanako receives broken data: 12**56**9
3. Hanako requests 3478 (looping)
4. Taro receives resend request
5. Taro then sends 3,4,7,8,8,8,8,8,8
6. Hanako receives 3**8
7. Hanako requests 47
8. Taro Sends 4,7,4,7,4,7,4,7,...
9. Hanako Sends <Fin>
10. Taro then goes to next file.

resend req packets: header(typ, fileno)/payload(0,1,2,3,4,5,6,7,8,11,12,13,14,15,16,17,19,20,25,26,43,44,46,47,48,49,50,53,54,56,59,62,64,65,66,67)

"""
