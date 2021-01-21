from queue import Queue
import socket
import threading
from enum import Enum
import random

from packet import SCUPacketType, SCUHeader, SCUPacket
import utils


class SCUMode(Enum):
    SendMode = 0
    RecvMode = 1


class SCU:
    def __init__(self, mtu=1500):
        self.mtu = mtu

    def bind_as_sender(self, receiver_address):
        self.mode = SCUMode.SendMode
        self.connection_manager = {}

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver_address = receiver_address
        self.lock = threading.Lock()

        sender_packet_loop_thread = threading.Thread(
            target=self._sender_packet_loop)
        sender_packet_loop_thread.setDaemon(True)
        sender_packet_loop_thread.start()

    def bind_as_receiver(self, receiver_address):
        self.mode = SCUMode.RecvMode
        self.received_files_data = {}

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(receiver_address)

        self.file_received = Queue()
        self.task_manager = Queue()
        receiver_packet_loop_thread = threading.Thread(
            target=self._receiver_packet_loop)
        receiver_packet_loop_thread.setDaemon(True)
        receiver_packet_loop_thread.start()
        recv_controller = threading.Thread(target =self._receiver_controller)
        recv_controller.setDaemon(True)
        recv_controller.start()

        # recv_controller.join()
        # receiver_packet_loop_thread.join()


    def drop(self):
        if self.mode == SCUMode.SendMode:
            self.connection_manager.clear()
            self.socket.close()

    def _sender_packet_loop(self):
        if self.mode == SCUMode.RecvMode:
            raise Exception
        while True:
            try:
                packet = SCUPacket()
                packet.from_raw(self.socket.recv(2048))
                # psuedo packet loss
                if random.random() >= 0.5:
                    continue
                if packet.header.id not in self.connection_manager:
                    continue
                if packet.header.typ == SCUPacketType.Fin.value:
                    self.connection_manager[packet.header.id].put((True, packet.header.seq))
                elif packet.header.typ == SCUPacketType.Rtr.value:
                    self.connection_manager[packet.header.id].put((False, packet.header.seq))
            except Exception as e: # recvが失敗した時とputが失敗した時は(適当)
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()

    def send(self, filepath, id):  # will lock the thread
        if self.mode == SCUMode.RecvMode:
            raise Exception
        queue = Queue()
        self.connection_manager[id] = queue  # コネクションを登録

        data_fragments = utils.split_file_into_mtu(filepath, self.mtu)

        all_packets = []
        count = 0
        for (seq, df) in enumerate(data_fragments):
            # create header
            header = SCUHeader()
            if seq == len(data_fragments) - 1:
                header.from_dict(
                    {"typ": SCUPacketType.DataEnd.value, "id": id, "seq": seq, "resendID": 1})
            else:
                header.from_dict(
                    {"typ": SCUPacketType.Data.value, "id": id, "seq": seq, "resendID": 1})
            # create packet
            packet = SCUPacket()
            packet.from_dict({"header": header, "payload": df, })

            all_packets.append(packet)
        while True:
            count += 1
            for seq in range(len(all_packets)):
                with self.lock:  # 複数のsendメソッドが並列に同時実行されている可能性があるため，ロックが必要
                    self.socket.sendto(
                        all_packets[seq].raw(), self.receiver_address)  # パケット送信
            with self.lock:  # 複数のsendメソッドが並列に同時実行されている可能性があるため，ロックが必要
                self.socket.sendto(
                    all_packets[-1].raw(), self.receiver_address)  # パケット送信
            if count > 200:
                break
            try:
                # queueを見てなにかあったら何かする
                # done!, 又は
                # しかるべき記録をして次のファイルに行く。
                #  来てないパケットのリストが来てる
                # そいつらを順番に送る。
                # もし長さがn以下だったらendしないで送り続ける。doneが来るまでは。
                pass
            except Exception as e:
                # queueになんもなかったらEndか何かを送り続ける
                pass
        return
        retransmit_seq = 0  # 再送の必要があるパケットを管理(どこまで受け取れてるか)
        seq = 0
        while True:
            try:
                while True:
                    try:
                        fin, sq = queue.get(block=False)  # 再送要求か受信完了報告か
                        if fin:  # 送信完了
                            del(self.connection_manager[id])  # コネクションを解除
                            return
                        elif sq < len(all_packets):  # 再送要求
                            retransmit_seq = max(sq, retransmit_seq)
                    except Exception as e:  # キューが空の時
                        if e == KeyboardInterrupt:
                            raise KeyboardInterrupt
                        else:
                            break
                with self.lock:  # 複数のsendメソッドが並列に同時実行されている可能性があるため，ロックが必要
                    self.socket.sendto(
                        all_packets[seq].raw(), self.receiver_address)  # パケット送信
                seq = max(seq + 1, retransmit_seq)  # seq更新
                if seq >= len(all_packets):
                    seq = retransmit_seq
            except Exception as e:  # sendtoが失敗した時は(適当)
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()

    def _receiver_controller(self):
        print('hi')
        received_files_flag = {}
        file_lengths = {}
        resend_id_count = {}
        while True:
            try:
                while True:
                    try:
                        # get packet
                        packet, key, from_addr = self.task_manager.get(block=False)
                        # key_ = max(key, -1)
                        # print(packet)
                    except Exception as e:  # キューが空の時
                        if e == KeyboardInterrupt:
                            raise KeyboardInterrupt
                        else:
                            break
                    else: # when try succeeds, if there are incoming packets
                        # なにかしらのデータがきてたら
                        if packet.header.typ == SCUPacketType.DataEnd.value or packet.header.typ == SCUPacketType.Data.value or packet.header.typ == SCUPacketType.End.value:
                            # ただ保存 TODO: ここでええんか？
                            self.received_files_data[key][packet.header.seq] = packet.payload
                            # DataEnd だったら(1回目で絶対拾える)
                            if packet.header.typ == SCUPacketType.DataEnd.value:
                                if not file_lengths[key]:
                                    file_lengths[key] = packet.header.seq + 1
                                    initial_resendID = 0
                                    resend_id_count[key] = initial_resendID
                                    # きてないseqをresendIDと共にそのIDの何かがくるまで全部送り続ける。
                                    # "12,23,24"
                                    unreceived_seqs_str = self.calculate_rtr(
                                        key, packet.header.seq)
                                    
                                    # while True:
                                    #     self.response(SCUPacketType.Rtr.value,
                                    #                   from_addr, packet.header.id, 0, initial_resendID, unreceived_seqs_str)
                                    #     # TODO: queueになにか来たら止めたいのだが
                                    #     # きてないパケットたちがn以下だったら次の何かが来るまでdone送る
                                else:  # dataendあるのにdataend送ってき
                                    pass
                            
                # do something
                # if key_:
                #     print(key_)
                pass
                # prev_packet = packet
                # prev_key = key
                # # when no list prepared for data
                # if key not in self.received_files_data:
                #     self.received_files_data[key] = [b""]*150
                #     received_files_flag[key] = False
                # # もうファイルが揃ってたら次のファイルが来るまで送り続ける
                # if received_files_flag[key]:
                #     self.response(SCUPacketType.Fin.value, from_addr, packet.header.id, 0, 0)
                #     # これ次のなにかがくるまでずっと送り続けたい
                #     continue
            except Exception as e:  # sendtoが失敗した時は(適当)
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()
                    # print('wow')
                    # なんか送る                    
                    # break

    # receives packet, unpack it and add to Queue (task_manager)
    def _receiver_packet_loop(self):
        if self.mode == SCUMode.SendMode:
            raise Exception
        while True:
            try:
                data, from_addr = self.socket.recvfrom(2048)
                # psuedo packet loss
                if random.random() >= 0.5:
                    continue
                packet = SCUPacket()
                packet.from_raw(data)
                key = utils.endpoint2str(from_addr, packet.header.id)
                # putting it to task_manager Queue()
                self.task_manager.put((packet, key, from_addr))
            except Exception as e:  # when recv fails
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()


                """
                # なにかしらのデータがきてたら
                if packet.header.typ == SCUPacketType.DataEnd.value or packet.header.typ == SCUPacketType.Data.value or packet.header.typ == SCUPacketType.End.value:

                    # endじゃなかったとき
                    # endが来るまで待ち。

                    if packet.header.typ == SCUPacketType.Data.value:
                        # 2回目以降のオペレーション終了お知らせ
                        pass

                    # 自分以前に何個受け取れてないか計算して、n以下だったら
                    rtr = self.calculate_rtr(key, packet.header.seq)
                    if rtr is not None:  # 再送要求する必要あり
                        self.response(SCUPacketType.Rtr.value,
                                      from_addr, packet.header.id, rtr, 0)
                    # ファイル受信完了
                    elif key in file_lengths and self.is_all_received(key, file_lengths[key]):
                        received_files_flag[key] = True
                        self.response(SCUPacketType.Fin.value,
                                      from_addr, packet.header.id, 0, 0)
                        self.file_received.put((key, file_lengths[key]))
"""



    def calculate_rtr(self, key, seq):
        unreceived_seqs = []
        for sq in range(0, seq):
            if not self.received_files_data[key][sq]:
                unreceived_seqs.append(sq)
        return ",".join(unreceived_seqs)

    def is_all_received(self, key, length):
        for i in range(0, length):
            if not self.received_files_data[key][i]:
                return False
        return True

    def response(self, typ, addr, id, rtr, resendID, *content):
        if self.mode == SCUMode.SendMode:
            raise Exception
        if typ == SCUPacketType.Rtr.value:
            header = SCUHeader()
            header.from_dict(
                {"typ": typ, "id": id, "seq": rtr, "resendID": resendID})
            packet = SCUPacket()
            payload = content if content else b''
            packet.from_dict({"header": header, "payload": payload, })
            self.socket.sendto(packet.raw(), addr)

        elif typ == SCUPacketType.Fin.value:
            header = SCUHeader()
            header.from_dict(
                {"typ": typ, "id": id, "seq": rtr, "resendID": resendID})
            packet = SCUPacket()
            packet.from_dict({"header": header, "payload": b'', })
            self.socket.sendto(packet.raw(), addr)

    def recv(self):
        if self.mode == SCUMode.SendMode:
            raise Exception
        key, length = self.file_received.get()
        return utils.fold_data(self.received_files_data[key], length)
