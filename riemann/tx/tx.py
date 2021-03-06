import riemann
from riemann import utils
from riemann.tx import shared
from riemann.script import serialization
from riemann.tx.shared import ByteData, VarInt

from typing import List, Optional, overload, Sequence, Tuple


class Outpoint(ByteData):
    '''
    An outpoint. A pointer to the previous output being consumed by the
    associated input. It specifies the prevout by transaction id and index
    within that transaction's output vector

    NB: Args must be little-endian

    Args:
        tx_id: the 32-byte LE hash of the previous transaction
        index: the 4-byte LE encoded index of the prevout in its transaction

    Attributes:
        tx_id: the 32-byte LE hash of the previous transaction
        index: the 4-byte LE encoded index of the prevout in its transaction
    '''

    tx_id: bytes
    index: bytes

    def __init__(self, tx_id: bytes, index: bytes):
        super().__init__()

        self.validate_bytes(tx_id, 32)
        self.validate_bytes(index, 4)

        self += tx_id
        self += index

        self.tx_id = tx_id
        self.index = index

        self._make_immutable()

    def copy(self,
             tx_id: Optional[bytes] = None,
             index: Optional[bytes] = None) -> 'Outpoint':
        '''
        Make a new copy of the object with optional modifications.
        '''
        return Outpoint(
            tx_id=tx_id if tx_id is not None else self.tx_id,
            index=index if index is not None else self.index)

    @classmethod
    def null(Outpoint) -> 'Outpoint':
        '''Make a null outpoint, as found in coinbase transactions'''
        return Outpoint(b'\x00' * 32, b'\xff' * 4)

    @classmethod
    def from_bytes(Outpoint, byte_string: bytes) -> 'Outpoint':
        '''
        Parse an Outpoint from a bytestring. Also available as from_hex
        '''
        return Outpoint(
            tx_id=byte_string[:32],
            index=byte_string[32:36])


class TxIn(ByteData):
    '''
    A transaction input, composed of an outpoint, a script_sig and a sequence
    number. Legacy TxIn script sigs contain spend authorization information for
    the referenced UTXO. Compatibility TxIn script sigs contain the witness
    program only. Segwit Txin script sigs are empty.

    The sequence number is used to set relative timelocks. See
    `this blog post <https://prestwi.ch/bitcoin-time-locks/>`_ for details.

    Args:
        outpoint: The `Outpoint` object pointing to the prevout being consumed
                  by this input
        stack_script: The Script program that sets the initial stack, if any.
                      Legacy inputs are unsigned without this. Segwit inputs
                      never have this.
        redeem_script: The Script program that controls spending, if any. Only
                       present when spending a Legacy or Compatibility SH UTXO.
                       Segwit inputs never have this.
        sequence: The 4-byte LE encoded sequence number of the input. Can be
                  used to set relative timelocks.

    Attributes:
        outpoint: The `Outpoint` object pointing to the prevout being consumed
                  by this input
        stack_script: The Script program that sets the initial stack, if any.
                      Legacy inputs are unsigned without this. Segwit inputs
                      never have this.
        redeem_script: The Script program that controls spending, if any. Only
                       present when spending a Legacy or Compatibility SH UTXO.
                       Segwit inputs never have this.
        script_sig: The combined stack_script and redeem_script. For Legacy and
                    Compatibility PKH transactions this will be equal to the
                    stack script.
        sequence: The 4-byte LE encoded sequence number of the input. Can be
                  used to set relative timelocks.
    '''

    outpoint: Outpoint
    stack_script: bytes
    redeem_script: bytes
    sequence: bytes

    def __init__(self,
                 outpoint: Outpoint,
                 stack_script: bytes,
                 redeem_script: bytes,
                 sequence: bytes):
        super().__init__()

        self.validate_bytes(outpoint, 36)
        self.validate_bytes(stack_script, None)
        self.validate_bytes(redeem_script, None)
        self.validate_bytes(sequence, 4)

        if len(stack_script) + len(redeem_script) > 1650:
            raise ValueError('Input script_sig is too long. '
                             'Expected <= 1650 bytes. Got {} bytes.'
                             .format(len(stack_script) + len(redeem_script)))

        self += outpoint
        self += VarInt(len(stack_script) + len(redeem_script))
        self += stack_script
        self += redeem_script
        self += sequence

        self.outpoint = outpoint
        self.stack_script = stack_script
        self.redeem_script = redeem_script
        self.script_sig = self.stack_script + self.redeem_script
        self.sequence = sequence

        self._make_immutable()

    def copy(self,
             outpoint: Optional[Outpoint] = None,
             stack_script: Optional[bytes] = None,
             redeem_script: Optional[bytes] = None,
             sequence: Optional[bytes] = None) -> 'TxIn':
        '''
        Make a new copy of the object with optional modifications.
        '''
        return TxIn(
            outpoint=outpoint if outpoint is not None else self.outpoint,
            stack_script=(stack_script if stack_script is not None
                          else self.stack_script),
            redeem_script=(redeem_script if redeem_script is not None
                           else self.redeem_script),
            sequence=sequence if sequence is not None else self.sequence)

    def is_p2sh(self) -> bool:
        '''
        Return True if the TxIn has a non-empty `redeem_script`
        '''
        return self.redeem_script != b''

    @staticmethod
    def _parse_script_sig(script_sig: bytes) -> Tuple[bytes, bytes]:
        '''
        Parse the scriptsig into a stack script and a redeem_script
        byte_string -> (byte_string, byte_string)
        '''
        # Is there a better way to do this?
        stack_script = script_sig
        redeem_script = b''
        try:
            # If the last entry deserializes, it's a p2sh input
            # There is a vanishingly small edge case where the pubkey
            #   forms a deserializable script.
            # Edge case: serialization errors on CODESEPARATOR
            deserialized = serialization.deserialize(script_sig)
            items = deserialized.split()
            serialization.hex_deserialize(items[-1])
            stack_script = serialization.serialize(' '.join(items[:-1]))
            redeem_script = serialization.serialize(items[-1])
        except (IndexError, ValueError, NotImplementedError):
            pass
        return stack_script, redeem_script

    @classmethod
    def from_bytes(TxIn, byte_string: bytes) -> 'TxIn':
        '''
        Parse a TxIn from a bytestring. Also available as from_hex
        '''
        outpoint = Outpoint.from_bytes(byte_string[:36])

        # Extract the script sig length
        script_sig_len = VarInt.from_bytes(byte_string[36:45])

        # Extract the script sig
        script_start = 36 + len(script_sig_len)
        script_end = script_start + script_sig_len.number
        script_sig = byte_string[script_start:script_end]

        sequence = byte_string[script_end:script_end + 4]

        # If the script-sig is blank, both stack and redeem are blank
        if script_sig == b'':
            stack_script = b''
            redeem_script = b''
        # If the outpoint is null (coinbase), don't try parsing the script sig
        elif outpoint == Outpoint.null():
            stack_script = script_sig
            redeem_script = b''
        # Parse the script sig into stack and redeem
        else:
            stack_script, redeem_script = TxIn._parse_script_sig(script_sig)
        return TxIn(
            outpoint=outpoint,
            stack_script=stack_script,
            redeem_script=redeem_script,
            sequence=sequence)


class TxOut(ByteData):
    '''
    A transaction output, composed of a value (in satoshi) and an output script
    describing the spend conditions on the new UTXO.

    Value is serialized as an 8-byte LE integer, measured in satoshi. Use the
    `i2le_padded` function in :ref:`utils` to serialize integers.

    TxOut accepts un-prepended output scripts, and adds their length for you.

    Args:
        value: the 8-byte LE encoded value of the output (in satoshi)
        output_script: the non-length-prepended output script as a bytestring

    Attributes:
        value: the 8-byte LE encoded value of the output (in satoshi)
        output_script: the non-length-prepended output script as a bytestring
    '''

    value: bytes
    output_script: bytes

    def __init__(self, value: bytes, output_script: bytes):
        super().__init__()

        self.validate_bytes(value, 8)
        self.validate_bytes(output_script, None)

        self += value
        self += VarInt(len(output_script))
        self += output_script

        self.value = value
        self.output_script = output_script

        self._make_immutable()

    def copy(self,
             value: Optional[bytes] = None,
             output_script: Optional[bytes] = None) -> 'TxOut':
        '''
        Make a new copy of the object with optional modifications.
        '''
        return TxOut(
            value=value if value is not None else self.value,
            output_script=(output_script if output_script is not None
                           else self.output_script))

    @classmethod
    def from_bytes(TxOut, byte_string: bytes) -> 'TxOut':
        '''
        Parse a TxOut from a bytestring. Also available as from_hex
        '''
        n = VarInt.from_bytes(byte_string[8:])
        script_start = 8 + len(n)
        script_end = script_start + n.number
        if n.number < 0xfc:
            return TxOut(
                value=byte_string[:8],
                output_script=byte_string[script_start:script_end])
        else:
            raise NotImplementedError(
                'No support for abnormally long pk_scripts.')


class WitnessStackItem(ByteData):
    '''
    A witness stack item. Each input witness is composed of an initial stack
    to be evaluated by the witness program. Witnesses for P2WSH inputs have a
    serialized script as the last stack element.

    Args:
        item: the raw data to be placed on the stack

    Attributes:
        item: the raw data to be placed on the stack
    '''
    def __init__(self, item: bytes):
        super().__init__()

        self.validate_bytes(item, None)

        self += VarInt(len(item))
        self += item

        self.item = item

        self._make_immutable()

    @classmethod
    def from_bytes(WitnessStackItem, byte_string: bytes) -> 'WitnessStackItem':
        '''
        Parse a WitnessStackItem from a bytestring. Also available as from_hex
        '''
        n = VarInt.from_bytes(byte_string)
        item_start = len(n)
        item_end = item_start + n.number
        return WitnessStackItem(byte_string[item_start:item_end])


class InputWitness(ByteData):
    '''
    The witness for a Compatibility or Segwit TxIn. It consists of a stack that
    will be evaluated by the witness program, represented as an ordered list of
    `WitnessStackItem` objects.

    Args:
        stack: the ordered sequence of WitnessStackItems

    Attributes:
        stack: the ordered sequence of WitnessStackItems
    '''
    stack: Tuple[WitnessStackItem, ...]

    def __init__(self, stack: Sequence[WitnessStackItem]):
        '''
        list(WitnessStackItem) -> InputWitness
        '''
        super().__init__()
        for item in stack:
            if not isinstance(item, WitnessStackItem):
                raise ValueError(
                    'Invalid witness stack item. '
                    'Expected WitnessStackItem. Got {}'
                    .format(item))

        self += VarInt(len(stack))
        for item in stack:
            self += item

        self.stack = tuple(item for item in stack)

        self._make_immutable()

    @classmethod
    def from_bytes(InputWitness, byte_string: bytes) -> 'InputWitness':
        '''
        Parse an InputWitness from a bytestring. Also available as from_hex
        '''
        stack_items = VarInt.from_bytes(byte_string)
        item_start = len(stack_items)
        items: List[WitnessStackItem] = []
        while len(items) < stack_items.number:
            item = WitnessStackItem.from_bytes(byte_string[item_start:])
            item_start += len(item)
            items.append(item)
        return InputWitness(items)

    def copy(self,
             stack: Optional[List[WitnessStackItem]] = None) -> 'InputWitness':
        '''
        Make a new copy of the object with optional modifications.
        '''
        return InputWitness(
            stack=stack if stack is not None else self.stack)


class Tx(ByteData):
    '''
    A complete transaction. It consists of a version, a flag that indicates the
    presence of witnesses (and breaks legacy parsers), a length-prepended
    vector of `TxIn` objects, a length-prepended vector of `TxOut` objects, and
    a locktime number. Compatibility and Segwit transactions MUST contain the
    witness flag. Signed Compatibility and Segwit transactions will
    additionally contain a vector of `InputWitness` objects.

    This object provides a number of conveniences for interacting with
    transactions, including `tx_id` calculation, and sighash calculation.

    To serialize the transaction, call `to_bytes()` or `hex()`.

    Note:
        The `lock_time` field is used to set absolute timelocks.
        These are complex and confusing. See
        `this blog post <https://prestwi.ch/bitcoin-time-locks/>`_ for details.

    Args:
        version: the 4-byte LE version number. Must be 1 or 2. Setting to 1
                 deactivates relative lock times.
        tx_ins: the ordered sequence of TxIn objects representing TXOs.
                consumed by this transaction. Signed Legacy transaction will
                include spend authorization here.
        tx_outs: the ordered sequence of TxOut objects representing TXOs
                 created by this transaction.
        tx_witnesses: the ordered sequence of InputWitness objects associated
                      with this transaction. Always empty in Legacy
                      transactions. In Compatibility and Segwit transactions
                      there must be one witness per input.
        lock_time: the 4-byte LE locktime number. Setting this invokes the
                   absolute time lock system. If it is below 500,000,000 it is
                   interpreted as a blockheight before which the transaction is
                   invalid. If set above that, it is interpreted as a Unix
                   timestamp before which the transaction is invalid.

    Attributes:
        version: the 4-byte LE version number. Must be 1 or 2. Setting to 1
                 deactivates relative lock times.
        flag: the 2-byte witness transaction flag. Always empty for Legacy
              transaction, or '0001' for Compatibility and Witness transactions
        tx_ins: the ordered sequence of TxIn objects representing TXOs.
                consumed by this transaction. Signed Legacy transaction will
                include spend authorization here.
        tx_outs: the ordered sequence of TxOut objects representing TXOs
                 created by this transaction.
        tx_witnesses: the ordered sequence of InputWitness objects associated
                      with this transaction. Always empty in Legacy
                      transactions. In Compatibility and Segwit transactions
                      there must be one witness per input.
        lock_time: the 4-byte LE locktime number. Setting this invokes the
                   absolute time lock system. If it is below 500,000,000 it is
                   interpreted as a blockheight before which the transaction is
                   invalid. If set above that, it is interpreted as a Unix
                   timestamp before which the transaction is invalid.
        tx_id_le: the LE (in-protocol) hash committed to by the block header
                  transaction merkle tree.
        wtx_id_le: the LE (in-protocol) hash committed to by the coinbase
                   transaction witness merkle tree. Not present in Legacy
                   transactions.
        tx_id: the BE (block explorer or human-facing) tx_id.
        wtx_id: the BE (block explorer or human-facing) wtx_id. Not present in
                Legacy transactions.

    '''

    version: bytes
    flag: Optional[bytes]
    tx_ins: Tuple[TxIn, ...]
    tx_outs: Tuple[TxOut, ...]
    tx_witnesses: Optional[Tuple[InputWitness, ...]]
    lock_time: bytes
    tx_id_le: bytes
    wtx_id_le: Optional[bytes]
    tx_id: bytes
    wtx_id: Optional[bytes]

    def __init__(self,
                 version: bytes,
                 flag: Optional[bytes],
                 tx_ins: Sequence[TxIn],
                 tx_outs: Sequence[TxOut],
                 tx_witnesses: Optional[Sequence[InputWitness]],
                 lock_time: bytes):

        super().__init__()

        self.validate_bytes(version, 4)
        self.validate_bytes(lock_time, 4)

        if flag is not None:
            if flag != riemann.network.SEGWIT_TX_FLAG:
                raise ValueError(
                    'Invald segwit flag. Expected None or '
                    f'{riemann.network.SEGWIT_TX_FLAG.hex()}. '
                    f'Got: {flag.hex()}')

        if tx_witnesses is not None and len(tx_witnesses) != 0:
            if flag is None:
                raise ValueError('Got witnesses but no segwit flag.')
            if len(tx_witnesses) != len(tx_ins):
                raise ValueError(
                    'Witness and TxIn lists must be same length. '
                    'Got {} inputs and {} witnesses.'
                    .format(len(tx_ins), len(tx_witnesses)))
            for witness in tx_witnesses:
                if not isinstance(witness, InputWitness):
                    raise ValueError(
                        'Invalid InputWitness. '
                        'Expected instance of InputWitness. Got {}'
                        .format(type(witness)))

        if min(len(tx_ins), len(tx_outs)) == 0:
            raise ValueError('Too few inputs or outputs. Stop that.')

        for tx_in in tx_ins:
            if not isinstance(tx_in, TxIn):
                raise ValueError(
                    'Invalid TxIn. '
                    'Expected instance of TxIn. Got {}'
                    .format(type(tx_in).__name__))

        for tx_out in tx_outs:
            if not isinstance(tx_out, TxOut):
                raise ValueError(
                    'Invalid TxOut. '
                    'Expected instance of TxOut. Got {}'
                    .format(type(tx_out).__name__))

        self += version
        if flag is not None:
            self += flag
        self += VarInt(len(tx_ins))
        for tx_in in tx_ins:
            self += tx_in
        self += VarInt(len(tx_outs))
        for tx_out in tx_outs:
            self += tx_out
        if tx_witnesses is not None:
            for witness in tx_witnesses:
                self += witness
        self += lock_time

        self.version = version
        self.flag = flag
        self.tx_ins = tuple(tx_in for tx_in in tx_ins)
        self.tx_outs = tuple(tx_out for tx_out in tx_outs)
        self.tx_witnesses = \
            tuple(wit for wit in tx_witnesses) if tx_witnesses is not None \
            else None
        self.lock_time = lock_time

        if flag is not None:
            self.tx_id_le = utils.hash256(self.no_witness())
            self.wtx_id_le = utils.hash256(self.to_bytes())
            self.tx_id = utils.change_endianness(self.tx_id_le)
            self.wtx_id = utils.change_endianness(self.wtx_id_le)

        else:
            self.tx_id_le = utils.hash256(self.to_bytes())
            self.tx_id = utils.change_endianness(self.tx_id_le)
            self.wtx_id = None
            self.wtx_le = None

        self._make_immutable()

    @classmethod
    def from_hex(Tx, hex_string: str) -> 'Tx':
        '''Instantiate a Tx object from a hex string'''
        return Tx.from_bytes(bytes.fromhex(hex_string))

    @classmethod
    def from_bytes(Tx, byte_string: bytes) -> 'Tx':
        '''Instantiate a Tx object from a bytestring'''

        # Get the version number
        version = byte_string[0:4]

        # Check if this is a witness tx
        if byte_string[4:6] == riemann.network.SEGWIT_TX_FLAG:
            tx_ins_num_loc = 6
            flag = riemann.network.SEGWIT_TX_FLAG
        else:
            tx_ins_num_loc = 4
            flag = None

        # Get the length of the tx_in vector
        tx_ins = []
        tx_ins_num = VarInt.from_bytes(byte_string[tx_ins_num_loc:])

        # `current` is the index of next read
        current = tx_ins_num_loc + len(tx_ins_num)

        # Deserialize all tx_ins
        for _ in range(tx_ins_num.number):
            tx_in = TxIn.from_bytes(byte_string[current:])
            current += len(tx_in)
            tx_ins.append(tx_in)

        # Get the length of the tx_out vector
        tx_outs = []
        tx_outs_num = VarInt.from_bytes(byte_string[current:])

        # Deserialize all outputs
        current += len(tx_outs_num)
        for _ in range(tx_outs_num.number):
            tx_out = TxOut.from_bytes(byte_string[current:])
            current += len(tx_out)
            tx_outs.append(tx_out)

        # Deserialize all witnesses if necessary
        tx_witnesses: List[InputWitness] = []
        if flag and len(byte_string[current:]) > 4:
            tx_witnesses_num = tx_ins_num
            for _ in range(tx_witnesses_num.number):
                tx_witness = InputWitness.from_bytes(byte_string[current:])
                current += len(tx_witness)
                tx_witnesses.append(tx_witness)

        # Get the lock time and return a complete tx
        lock_time = byte_string[current:current + 4]
        return Tx(
            version=version,
            flag=flag,
            tx_ins=tx_ins,
            tx_outs=tx_outs,
            tx_witnesses=tx_witnesses,
            lock_time=lock_time)

    def no_witness(self) -> bytes:
        '''
        Return the Tx as a bytestring stripped of witnesses. This is the
        preimage of `tx_id` and `tx_id_le`.
        '''
        tx = bytes()
        tx += self.version
        tx += VarInt(len(self.tx_ins)).to_bytes()
        for tx_in in self.tx_ins:
            tx += tx_in.to_bytes()
        tx += VarInt(len(self.tx_outs)).to_bytes()
        for tx_out in self.tx_outs:
            tx += tx_out.to_bytes()
        tx += self.lock_time
        return tx

    def is_witness(self) -> bool:
        '''Return True if the transaction witness flag is set'''
        return self.flag is not None or self.tx_witnesses is not None

    def calculate_fee(self, input_values: Sequence[int]) -> int:
        '''
        Calculate the fee associated with a transaction. Caller must provide a
        sequence representing the value (in satoshi) of each input.

        Args:
            input_values: The value of each input in order.
        Returns:
            The total fee paid to miners by this transaction.
        '''
        return \
            sum(input_values) \
            - sum([utils.le2i(o.value) for o in self.tx_outs])

    def sighash_none(self) -> bytes:
        '''SIGHASH_NONE is a bad idea.'''
        raise NotImplementedError('SIGHASH_NONE is a bad idea.')

    def copy(self,
             version: Optional[bytes] = None,
             flag: Optional[bytes] = None,
             tx_ins: Optional[Sequence[TxIn]] = None,
             tx_outs: Optional[Sequence[TxOut]] = None,
             tx_witnesses: Optional[Sequence[InputWitness]] = None,
             lock_time: Optional[bytes] = None) -> 'Tx':
        '''
        Make a new copy of the object with optional modifications.
        '''
        return Tx(version=version if version is not None else self.version,
                  flag=flag if flag is not None else self.flag,
                  tx_ins=tx_ins if tx_ins is not None else self.tx_ins,
                  tx_outs=tx_outs if tx_outs is not None else self.tx_outs,
                  tx_witnesses=(tx_witnesses if tx_witnesses is not None
                                else self.tx_witnesses),
                  lock_time=(lock_time if lock_time is not None
                             else self.lock_time))

    def _sighash_prep(self, index: int, script: bytes) -> 'Tx':
        '''
        Sighashes suck
        Performs the sighash setup described here:
        https://en.bitcoin.it/wiki/OP_CHECKSIG#How_it_works
        https://bitcoin.stackexchange.com/questions/3374/how-to-redeem-a-basic-tx
        We save on complexity by refusing to support OP_CODESEPARATOR
        '''
        # 0 out scripts in tx_ins
        copy_tx_ins = [tx_in.copy(stack_script=b'', redeem_script=b'')
                       for tx_in in self.tx_ins]

        # NB: The script for the current transaction input in txCopy is set to
        #     subScript (lead in by its length as a var-integer encoded!)
        to_strip = VarInt.from_bytes(script)
        copy_tx_ins[index] = \
            copy_tx_ins[index].copy(redeem_script=script[len(to_strip):])

        return self.copy(tx_ins=copy_tx_ins)

    @overload
    def sighash_all(self,
                    index: int,
                    script: bytes,
                    prevout_value: bytes,
                    anyone_can_pay: bool) -> bytes:
        ...

    @overload  # noqa: F811
    def sighash_all(self,
                    index: int,
                    script: bytes,
                    anyone_can_pay: bool) -> bytes:
        ...

    @overload  # noqa: F811
    def sighash_all(self,
                    index: int,
                    script: bytes,
                    prevout_value: bytes) -> bytes:
        ...

    @overload  # noqa: F811
    def sighash_all(self,
                    index: int,
                    script: bytes) -> bytes:
        ...

    def sighash_all(self,  # noqa: F811
                    index,
                    script,
                    prevout_value=None,
                    anyone_can_pay=False) -> bytes:
        '''
        Calculate the hash to be signed when adding authorization information
        (a script sig or a witness) to an input using SIGHASH_ALL.

        SIGHASH_ALL commits to ALL inputs, and ALL outputs. It indicates that
        no further modification of the transaction is allowed without
        invalidating the signature.

        SIGHASH_ALL + ANYONECANPAY commits to ONE input and ALL outputs. It
        indicates that anyone may add additional value to the transaction, but
        that no one may modify the payments made. Any extra value added above
        the sum of output values will be given to miners as part of the tx fee.

        We must specify the index of the input in the `tx_ins` sequence, the
        script controlling the TXO being spent by the input, and whether to use
        the ANYONECANPAY sighash modifier. Compatibility and Witness inputs
        must additionally supply the value of the TXO being consumed.

        This function automatically selects between Legacy, Witness, and Bcash
        SIGHASH_FORKID based on the network selected, and whether the witness
        flag is present in the transaction.

        For Legacy sighash documentation, see here:

        - https://en.bitcoin.it/wiki/OP_CHECKSIG#Hashtype_SIGHASH_ALL_.28default.29

        For BIP143 (Witness and Compatibility) documentation, see here:

        - https://github.com/bitcoin/bips/blob/master/bip-0143.mediawiki

        For the BitcoinCash specific rip-off of BIP143 documentation, see here:

        - https://github.com/bitcoincashorg/spec/blob/master/replay-protected-sighash.md

        Note:
            After signing the digest, you MUST append the sighash indicator
            byte to the resulting signature. This will be 0x01 (SIGHASH_ALL) or
            0x81 (SIGHASH_ALL + SIGHASH_ANYONECANPAY).

        Args:
            index: The index of the input being authorized
            script: The length-prepended script associated with the TXO being
                    spent. For PKH outputs this will be a pkh spend script (
                    i.e. '1976a914....88ac'). For SH outputs this will be the
                    redeem_script (Legacy) or Witness Script (Compatibility and
                    Segwit). If the TXO being spent has a non-standard output
                    script, use that here.
            prevout_value: The 8-byte LE integer-encoded value of the prevout
            anyone_can_pay: True if using the ANYONECANPAY sighash modifier

        Returns:
            The 32-byte digest to be signed.
        '''  # noqa: E501

        if riemann.network.FORKID is not None:
            return self._sighash_forkid(index=index,
                                        script=script,
                                        prevout_value=prevout_value,
                                        sighash_type=shared.SIGHASH_ALL,
                                        anyone_can_pay=anyone_can_pay)

        if self.is_witness():
            return self.segwit_sighash(
                index=index,
                script=script,
                prevout_value=prevout_value,
                sighash_type=shared.SIGHASH_ALL,
                anyone_can_pay=anyone_can_pay)

        copy_tx = self._sighash_prep(index=index, script=script)
        if anyone_can_pay:
            return self._sighash_anyone_can_pay(
                index=index, copy_tx=copy_tx, sighash_type=shared.SIGHASH_ALL)

        return self._sighash_final_hashing(copy_tx, shared.SIGHASH_ALL)

    @overload
    def sighash_single(self,
                       index: int,
                       script: bytes,
                       prevout_value: bytes,
                       anyone_can_pay: bool) -> bytes:
        ...

    @overload  # noqa: F811
    def sighash_single(self,
                       index: int,
                       script: bytes,
                       anyone_can_pay: bool) -> bytes:
        ...

    @overload  # noqa: F811
    def sighash_single(self,
                       index: int,
                       script: bytes,
                       prevout_value: bytes) -> bytes:
        ...

    @overload  # noqa: F811
    def sighash_single(self,
                       index: int,
                       script: bytes) -> bytes:
        ...

    def sighash_single(self,  # noqa: F811
                       index,
                       script,
                       prevout_value=None,
                       anyone_can_pay=False):
        '''
        Calculate the hash to be signed when adding authorization information
        (a script sig or a witness) to an input using SIGHASH_SINGLE.

        SIGHASH_SINGLE commits to ALL inputs, and ONE output. It indicates that/
        anyone may append additional outputs to the transaction to reroute
        funds from the inputs. Additional inputs cannot be added without
        invalidating the signature. It is logically difficult to use securely,
        as it consents to funds being moved, without specifying their
        destination.

        SIGHASH_SINGLE commits specifically the the output at the same index as
        the input being signed. If there is no output at that index, (because,
        e.g. the input vector is longer than the output vector) it behaves
        insecurely, and we do not implement that protocol bug.

        SIGHASH_SINGLE + ANYONECANPAY commits to ONE input and ONE output. It
        indicates that anyone may add additional value to the transaction, and
        route value to any other location. The signed input and output must be
        included in the fully-formed transaction at the same index in their
        respective vectors.

        When the input is larger than the output, a partial transaction signed
        this way cedes the difference to whoever cares to construct a complete
        transaction. However, when the output is larger than the input, it
        functions as a one-time-use payment invoice. Anyone may consume the
        input by adding value. This is useful for addressing race conditions in
        certain cross-chain protocols that the author of this documentation
        invented. :)

        We must specify the index of the input in the `tx_ins` sequence, the
        script controlling the TXO being spent by the input, and whether to use
        the ANYONECANPAY sighash modifier. Compatibility and Witness inputs
        must additionally supply the value of the TXO being consumed.

        This function automatically selects between Legacy, Witness, and Bcash
        SIGHASH_FORKID based on the network selected, and whether the witness
        flag is present in the transaction.

        For Legacy sighash documentation, see here:

        - https://en.bitcoin.it/wiki/OP_CHECKSIG#Procedure_for_Hashtype_SIGHASH_SINGLE
        - https://bitcoin.stackexchange.com/questions/3890/for-sighash-single-do-the-outputs-other-than-at-the-input-index-have-8-bytes-or
        - https://github.com/petertodd/python-bitcoinlib/blob/051ec4e28c1f6404fd46713c2810d4ebbed38de4/bitcoin/core/script.py#L913-L965

        For BIP143 (Witness and Compatibility) documentation, see here:

        - https://github.com/bitcoin/bips/blob/master/bip-0143.mediawiki

        For the BitcoinCash specific rip-off of BIP143 documentation, see here:

        - https://github.com/bitcoincashorg/spec/blob/master/replay-protected-sighash.md

        Note:
           After signing the digest, you MUST append the sighash indicator
           byte to the resulting signature. This will be 0x03 (SIGHASH_SINGLE)
           or 0x83 (SIGHASH_SINGLE + SIGHASH_ANYONECANPAY).

        Args:
           index: The index of the input being authorized
           script: The length-prepended script associated with the TXO being
                   spent. For PKH outputs this will be a pkh spend script (
                   i.e. '1976a914....88ac'). For SH outputs this will be the
                   redeem_script (Legacy) or Witness Script (Compatibility and
                   Segwit). If the TXO being spent has a non-standard output
                   script, use that here.
           prevout_value: The 8-byte LE integer-encoded value of the prevout
           anyone_can_pay: True if using the ANYONECANPAY sighash modifier

        Returns:
           The 32-byte digest to be signed.
        '''  # noqa: E501

        if index >= len(self.tx_outs):
            raise NotImplementedError(
                'I refuse to implement the SIGHASH_SINGLE bug.')

        if riemann.network.FORKID is not None:
            return self._sighash_forkid(index=index,
                                        script=script,
                                        prevout_value=prevout_value,
                                        sighash_type=shared.SIGHASH_SINGLE,
                                        anyone_can_pay=anyone_can_pay)

        if self.is_witness():
            return self.segwit_sighash(
                index=index,
                script=script,
                prevout_value=prevout_value,
                sighash_type=shared.SIGHASH_SINGLE,
                anyone_can_pay=anyone_can_pay)

        copy_tx = self._sighash_prep(index=index, script=script)

        # Remove outputs after the one we're signing
        # Other tx_outs are set to -1 value and null scripts
        copy_tx_outs = list(copy_tx.tx_outs[:index + 1])
        copy_tx_outs = [TxOut(value=b'\xff' * 8, output_script=b'')
                        for _ in copy_tx.tx_ins]  # Null them all
        copy_tx_outs[index] = copy_tx.tx_outs[index]  # Fix the current one

        # Other tx_ins sequence numbers are set to 0
        copy_tx_ins = [tx_in.copy(sequence=b'\x00\x00\x00\x00')
                       for tx_in in copy_tx.tx_ins]  # Set all to 0
        copy_tx_ins[index] = copy_tx.tx_ins[index]  # Fix the current one

        copy_tx = copy_tx.copy(
            tx_ins=copy_tx_ins,
            tx_outs=copy_tx_outs)

        if anyone_can_pay:  # Forward onwards
            return self._sighash_anyone_can_pay(
                index, copy_tx, shared.SIGHASH_SINGLE)

        return self._sighash_final_hashing(copy_tx, shared.SIGHASH_SINGLE)

    def segwit_sighash(self,
                       index: int,
                       sighash_type: int,
                       prevout_value: bytes,
                       script: bytes,
                       anyone_can_pay: bool = False) -> bytes:
        '''
        Implements bip143 (witness) sighash. Prefer calling `sighash_all` or
        `sighash_single`.

        For documentation see here:
        https://github.com/bitcoin/bips/blob/master/bip-0143.mediawiki

        For an excellent pasta dinner, see here:
        https://ricette.giallozafferano.it/Spaghetti-alla-Norma.html
        '''
        data = ByteData()

        # 1. nVersion of the transaction (4-byte little endian)
        data += self.version

        # 2. hashPrevouts (32-byte hash)
        data += self._hash_prevouts(anyone_can_pay=anyone_can_pay)

        # 3. hashSequence (32-byte hash)
        data += self._hash_sequence(sighash_type=sighash_type,
                                    anyone_can_pay=anyone_can_pay)

        # 4. outpoint (32-byte hash + 4-byte little endian)
        data += self.tx_ins[index].outpoint

        # 5. scriptCode of the input (serialized as scripts inside CTxOuts)
        data += script

        # 6. value of the output spent by this input (8-byte little endian)
        data += prevout_value

        # 7. nSequence of the input (4-byte little endian)
        data += self.tx_ins[index].sequence

        # 8. hashOutputs (32-byte hash)
        data += self._hash_outputs(index=index, sighash_type=sighash_type)

        # 9. nLocktime of the transaction (4-byte little endian)
        data += self.lock_time

        # 10. sighash type of the signature (4-byte little endian)
        data += self._segwit_sighash_adjustment(sighash_type=sighash_type,
                                                anyone_can_pay=anyone_can_pay)

        return utils.hash256(data.to_bytes())

    def _sighash_anyone_can_pay(
            self,
            index: int,
            copy_tx: 'Tx',
            sighash_type: int) -> bytes:
        '''
        int, byte-like, Tx, int -> bytes
        Applies SIGHASH_ANYONECANPAY procedure.
        Should be called by another SIGHASH procedure.
        Not on its own.
        https://en.bitcoin.it/wiki/OP_CHECKSIG#Procedure_for_Hashtype_SIGHASH_ANYONECANPAY
        '''
        # The txCopy input vector is resized to a length of one.
        copy_tx_ins = [copy_tx.tx_ins[index]]
        copy_tx = copy_tx.copy(tx_ins=copy_tx_ins)

        return self._sighash_final_hashing(
            copy_tx, sighash_type | shared.SIGHASH_ANYONECANPAY)

    def _sighash_final_hashing(
            self,
            copy_tx: 'Tx',
            sighash_type: int) -> bytes:
        '''
        Tx, int -> bytes
        Returns the hash that should be signed
        https://en.bitcoin.it/wiki/OP_CHECKSIG#Procedure_for_Hashtype_SIGHASH_ANYONECANPAY
        '''
        sighash = ByteData()
        sighash += copy_tx.to_bytes()
        sighash += utils.i2le_padded(sighash_type, 4)

        return utils.hash256(sighash.to_bytes())

    def _hash_prevouts(self, anyone_can_pay: bool) -> bytes:
        if anyone_can_pay:
            # If the ANYONECANPAY flag is set,
            # hashPrevouts is a uint256 of 0x0000......0000.
            hash_prevouts = b'\x00' * 32
        else:
            # hashPrevouts is the double SHA256 of all outpoints;
            outpoints = ByteData()
            for tx_in in self.tx_ins:
                outpoints += tx_in.outpoint
            hash_prevouts = utils.hash256(outpoints.to_bytes())
        return hash_prevouts

    def _hash_sequence(self, sighash_type: int, anyone_can_pay: bool) -> bytes:
        '''BIP143 hashSequence implementation

        Args:
            sighash_type    (int): SIGHASH_SINGLE or SIGHASH_ALL
            anyone_can_pay (bool): true if ANYONECANPAY should be set
        Returns:
            (bytes): the hashSequence, a 32 byte hash
        '''
        if anyone_can_pay or sighash_type == shared.SIGHASH_SINGLE:
            # If any of ANYONECANPAY, SINGLE sighash type is set,
            # hashSequence is a uint256 of 0x0000......0000.
            return b'\x00' * 32
        else:
            # hashSequence is the double SHA256 of nSequence of all inputs;
            sequences = ByteData()
            for tx_in in self.tx_ins:
                sequences += tx_in.sequence
            return utils.hash256(sequences.to_bytes())

    def _hash_outputs(self, index: int, sighash_type: int) -> bytes:
        '''BIP143 hashOutputs implementation

        Args:
            index        (int): index of input being signed
            sighash_type (int): SIGHASH_SINGLE or SIGHASH_ALL
        Returns:
            (bytes): the hashOutputs, a 32 byte hash
        '''
        if sighash_type == shared.SIGHASH_ALL:
            # If the sighash type is ALL,
            # hashOutputs is the double SHA256 of all output amounts
            # paired up with their scriptPubKey;
            outputs = ByteData()
            for tx_out in self.tx_outs:
                outputs += tx_out.to_bytes()
            return utils.hash256(outputs.to_bytes())
        elif (sighash_type == shared.SIGHASH_SINGLE
              and index < len(self.tx_outs)):
            # if sighash type is SINGLE
            # and the input index is smaller than the number of outputs,
            # hashOutputs is the double SHA256 of the output at the same index
            return utils.hash256(self.tx_outs[index].to_bytes())
        else:
            # Otherwise, hashOutputs is a uint256 of 0x0000......0000
            raise NotImplementedError(
                'I refuse to implement the SIGHASH_SINGLE bug.')

    def _forkid_sighash_adjustment(
            self, sighash_type: int, anyone_can_pay: bool) -> bytes:
        # The sighash type is altered to include a 24-bit fork id
        # ss << ((GetForkID() << 8) | nHashType)
        forkid = riemann.network.FORKID << 8
        sighash = forkid | sighash_type | shared.SIGHASH_FORKID
        if anyone_can_pay:
            sighash = sighash | shared.SIGHASH_ANYONECANPAY
        return utils.i2le_padded(sighash, 4)

    def _segwit_sighash_adjustment(
            self,
            sighash_type: int,
            anyone_can_pay: bool) -> bytes:
        # sighash type altered to include ANYONECANPAY
        if anyone_can_pay:
            sighash_type = sighash_type | shared.SIGHASH_ANYONECANPAY
        return utils.i2le_padded(sighash_type, 4)

    def _sighash_forkid(
            self,
            index: int,
            script: bytes,
            prevout_value: bytes,
            sighash_type: int,
            anyone_can_pay: bool = False):
        '''
        https://github.com/bitcoincashorg/spec/blob/master/replay-protected-sighash.md
        '''
        self.validate_bytes(prevout_value, 8)

        data = ByteData()

        # 1. nVersion of the transaction (4-byte little endian)
        data += self.version

        # 2. hashPrevouts (32-byte hash)
        data += self._hash_prevouts(anyone_can_pay=anyone_can_pay)

        # 3. hashSequence (32-byte hash)
        data += self._hash_sequence(sighash_type=sighash_type,
                                    anyone_can_pay=anyone_can_pay)

        # 4. outpoint (32-byte hash + 4-byte little endian)
        data += self.tx_ins[index].outpoint

        # 5. scriptCode of the input (serialized as scripts inside CTxOuts)
        data += script

        # 6. value of the output spent by this input (8-byte little endian)
        data += prevout_value

        # 7. nSequence of the input (4-byte little endian)
        data += self.tx_ins[index].sequence

        # 8. hashOutputs (32-byte hash)
        data += self._hash_outputs(index=index, sighash_type=sighash_type)

        # 9. nLocktime of the transaction (4-byte little endian)
        data += self.lock_time

        # 10. sighash type of the signature (4-byte little endian)
        data += self._forkid_sighash_adjustment(sighash_type=sighash_type,
                                                anyone_can_pay=anyone_can_pay)

        return utils.hash256(data.to_bytes())
