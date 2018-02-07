#=======================================================================
# storage.py
#=======================================================================

from pydgin.jit               import elidable, unroll_safe, hint
from debug                    import Debug, pad, pad_hex
from pydgin.utils             import r_uint, specialize, r_ulonglong
try:
  from rpython.rlib.rarithmetic import r_uint32, widen
except ImportError:
  # if rpython not in path, we can use normal ints to store data
  r_uint32 = int
  def widen( value ):
    return value

#-----------------------------------------------------------------------
# RegisterFile
#-----------------------------------------------------------------------
class RegisterFile( object ):
  def __init__( self, constant_zero=True, num_regs=32, nbits=32 ):
    self.num_regs = num_regs
    self.regs     = [ r_uint(0) ] * self.num_regs
    self.debug    = Debug()
    self.nbits    = nbits
    self.debug_nchars = nbits / 4

    if constant_zero: self._setitemimpl = self._set_item_const_zero
    else:             self._setitemimpl = self._set_item
  def __getitem__( self, idx ):
    if self.debug.enabled( "rf" ):
      print ':: RD.RF[%s] = %s' % (
                          pad( "%d" % idx, 2 ),
                          pad_hex( self.regs[idx],
                                   len=self.debug_nchars ) ),
    return self.regs[idx]

  @specialize.argtype(2)
  def __setitem__( self, idx, value ):
    value = r_uint( value )
    self._setitemimpl( idx, value )

  def _set_item( self, idx, value ):
    self.regs[idx] = value
    if self.debug.enabled( "rf" ):
      print ':: WR.RF[%s] = %s' % (
                        pad( "%d" % idx, 2 ),
                        pad_hex( self.regs[idx],
                                 len=self.debug_nchars ) ),
  def _set_item_const_zero( self, idx, value ):
    if idx != 0:
      self.regs[idx] = value
      if self.debug.enabled( "rf" ):
        print ':: WR.RF[%s] = %s' % (
                          pad( "%d" % idx, 2 ),
                          pad_hex( self.regs[idx],
                                   len=self.debug_nchars ) ),

  #-----------------------------------------------------------------------
  # print_regs
  #-----------------------------------------------------------------------
  # prints all registers (register dump)
  # per_row specifies the number of registers to display per row
  def print_regs( self, per_row=6 ):
    for c in xrange( 0, self.num_regs, per_row ):
      str = ""
      for r in xrange( c, min( self.num_regs, c+per_row ) ):
        str += "%s:%s " % ( pad( "%d" % r, 2 ),
                            pad_hex( self.regs[r] ) )
      print str

#-----------------------------------------------------------------------
# Memory
#-----------------------------------------------------------------------
def Memory( data=None, size=2**27, byte_storage=False, nbits=32 ):
  print "memory_size %x\n" % ( size )
  # use sparse storage if not translated
  try:
    from rpython.rlib.objectmodel import we_are_translated
    sparse_storage = not we_are_translated()
  except ImportError:
    sparse_storage = True

  if sparse_storage:
    print "NOTE: Using sparse storage"
    if byte_storage:
      return _SparseMemory( _ByteMemory, nbits=nbits )
    else:
      return _SparseMemory( _WordMemory, nbits=nbits )
  else:
    if byte_storage:
      return _ByteMemory( data, size, nbits=nbits )
    else:
      return _WordMemory( data, size, nbits=nbits )

#-------------------------------------------------------------------------
# _WordMemory
#-------------------------------------------------------------------------
# Memory that uses ints instead of chars
class _WordMemory( object ):
  _immutable_fields_ = [ 'nbits' ]
  def __init__( self, data=None, size=2**10, nbits=32 ):
    # we currently only support 32-bit and 64-bit words
    assert nbits == 32 or nbits == 64
    self.nbits = nbits
    if self.nbits == 32:
      self.data  = data if data else [ r_uint32(0) ] * (size >> 2)
      self.size  = r_uint(len( self.data ) << 2)
    elif self.nbits == 64:
      self.data  = data if data else [ r_ulonglong(0) ] * (size >> 3)
      self.size  = r_ulonglong(len( self.data ) << 3)
    self.debug = Debug()

    # TODO: pass data_section to memory for bounds checking
    self.data_section = 0x00000000

  def bounds_check( self, addr, x ):
    # check if the accessed data is larger than the memory size
    if addr > self.size:
      print ("WARNING: %s accessing larger address than memory size. "
             "addr=%s size=%s") % ( x, pad_hex( addr ), pad_hex( self.size ) )
      raise Exception()
    if addr == 0:
      print "WARNING: accessing null pointer!"
      raise Exception()

    # Special write checks
    if x == 'WR' and addr < r_uint( self.data_section ):
      print ("WARNING: %s writing address below .data section!!!. "
             "addr=%s size=%s") % ( x, pad_hex( addr ), pad_hex( self.data_section ) )
      raise Exception()


  @specialize.argtype(1)
  @unroll_safe
  def read( self, start_addr, num_bytes ):
    assert 0 < num_bytes <= 8
    if self.nbits == 32:
      start_addr = r_uint( start_addr )
      word = start_addr >> 2
      byte = start_addr &  0b11
    else: # self.nbits == 64:
      start_addr = r_ulonglong( start_addr )
      word = start_addr >> 3
      byte = start_addr &  0b111

    if self.debug.enabled( "mem" ):
      print ':: RD.MEM[%s] = ' % pad_hex( start_addr ),
    if self.debug.enabled( "memcheck" ):
      self.bounds_check( start_addr, 'RD' )

    value = 0
    if   num_bytes == 8:  # TODO: byte should only be 0 (only aligned)
      assert self.nbits == 64
      value = widen( self.data[ word ] )
    elif num_bytes == 4:  # TODO: byte should only be 0, 4 (only aligned)
      mask = 0xFFFFFFFF << (byte * 8)
      value = ( widen( self.data[ word ] ) & mask) >> (byte * 8)
    elif num_bytes == 2:  # TODO: byte should only be 0, 1, 2, not 3
      mask = 0xFFFF << (byte * 8)
      value = ( widen( self.data[ word ] ) & mask) >> (byte * 8)
    elif num_bytes == 1:
      mask = 0xFF   << (byte * 8)
      value = ( widen( self.data[ word ] ) & mask) >> (byte * 8)
    else:
      raise Exception('Invalid num_bytes: %d!' % num_bytes)

    if self.debug.enabled( "mem" ):
      print '%s' % pad_hex( value ),

    return r_uint( value )

  # this is instruction read, which is otherwise identical to read. The
  # only difference is the elidable annotation, which we assume the
  # instructions are not modified (no side effects, assumes the addresses
  # correspond to the same instructions)
  @elidable
  def iread( self, start_addr, num_bytes ):
    assert start_addr & 0b11 == 0  # only aligned accesses allowed
    if self.nbits == 32:
      assert num_bytes == 4
      return r_uint( widen( self.data[ start_addr >> 2 ] ) )
    else: # self.nbits == 64:
      if num_bytes == 8:
        return r_ulonglong( widen( self.data[ start_addr >> 3 ] ) )
      else:
        assert num_bytes == 4
        word = r_ulonglong( widen( self.data[ start_addr >> 3 ] ) )
        return 0xFFFFFFFF & ( word >> ( (start_addr & 0b111) * 8) )

  @specialize.argtype(1, 3)
  @unroll_safe
  def write( self, start_addr, num_bytes, value ):
    assert 0 < num_bytes <= 8
    if self.nbits == 32:
      start_addr = r_uint( start_addr )
      value = r_uint( value )
      word = start_addr >> 2
      byte = start_addr &  0b11
      word_mask = r_uint( 0xFFFFFFFF )
    else: # self.nbits == 64:
      start_addr = r_ulonglong( start_addr )
      value = r_ulonglong( value )
      word = start_addr >> 3
      byte = start_addr &  0b111
      word_mask = r_ulonglong( 0xFFFFFFFFFFFFFFFF )

    if self.debug.enabled( "memcheck" ):
      self.bounds_check( start_addr, 'WR' )

    if   num_bytes == 8:  # TODO: byte should only be 0 (only aligned)
      # no masking needed
      assert self.nbits == 64
    elif num_bytes == 4:
      # no masking needed for nbits == 32
      if self.nbits == 64:
        mask  = ~(0xFFFFFFFF << (byte * 8)) & word_mask
        value = ( widen( self.data[ word ] ) & mask ) \
                | ( (value & 0xFFFFFFFF) << (byte * 8) )
    elif num_bytes == 2:  # TODO: byte should only be 0, 1, 2, not 3
      mask  = ~(0xFFFF << (byte * 8)) & word_mask
      value = ( widen( self.data[ word ] ) & mask ) \
              | ( (value & 0xFFFF) << (byte * 8) )
    elif num_bytes == 1:
      mask  = ~(0xFF   << (byte * 8)) & word_mask
      value = ( widen( self.data[ word ] ) & mask ) \
              | ( (value & 0xFF  ) << (byte * 8) )
    else:
      raise Exception('Invalid num_bytes: %d!' % num_bytes)

    if self.debug.enabled( "mem" ):
      print ':: WR.MEM[%s] = %s' % ( pad_hex( start_addr ),
                                     pad_hex( value ) ),
    if self.nbits == 32:
      self.data[ word ] = r_uint32( value )
    else: # self.nbits == 64:
      self.data[ word ] = r_ulonglong( value )

#-----------------------------------------------------------------------
# _ByteMemory
#-----------------------------------------------------------------------
class _ByteMemory( object ):
  def __init__( self, data=None, size=2**10, nbits=32 ):
    self.data  = data if data else [' '] * size
    self.size  = len( self.data )
    self.nbits = nbits
    self.debug = Debug()

  def bounds_check( self, addr ):
    # check if the accessed data is larger than the memory size
    if addr > self.size:
      print "WARNING: accessing larger address than memory size. " + \
            "addr=%s size=%s" % ( pad_hex( addr ), pad_hex( self.size ) )
    if addr == 0:
      print "WARNING: writing null pointer!"
      raise Exception()

  @unroll_safe
  def read( self, start_addr, num_bytes ):
    if self.debug.enabled( "memcheck" ):
      self.bounds_check( start_addr )
    value = 0
    if self.debug.enabled( "mem" ):
      print ':: RD.MEM[%s] = ' % pad_hex( start_addr ),
    for i in range( num_bytes-1, -1, -1 ):
      value = value << 8
      value = value | ord( self.data[ start_addr + i ] )
    if self.debug.enabled( "mem" ):
      print '%s' % pad_hex( value ),
    return value

  # this is instruction read, which is otherwise identical to read. The
  # only difference is the elidable annotation, which we assume the
  # instructions are not modified (no side effects, assumes the addresses
  # correspond to the same instructions)
  @elidable
  def iread( self, start_addr, num_bytes ):
    value = 0
    for i in range( num_bytes-1, -1, -1 ):
      value = value << 8
      value = value | ord( self.data[ start_addr + i ] )
    return value

  @unroll_safe
  def write( self, start_addr, num_bytes, value ):
    if self.debug.enabled( "memcheck" ):
      self.bounds_check( start_addr )
    if self.debug.enabled( "mem" ):
      print ':: WR.MEM[%s] = %s' % ( pad_hex( start_addr ),
                                     pad_hex( value ) ),
    for i in range( num_bytes ):
      self.data[ start_addr + i ] = chr(value & 0xFF)
      value = value >> 8

#-----------------------------------------------------------------------
# _SparseMemory
#-----------------------------------------------------------------------

class _SparseMemory( object ):
  _immutable_fields_ = [ "BlockMemory", "block_size", "addr_mask",
                         "block_mask" ]

  def __init__( self, BlockMemory, block_size=2**10, nbits=32 ):
    self.BlockMemory = BlockMemory
    self.nbits      = nbits
    self.block_size = block_size
    self.addr_mask  = block_size - 1
    self.block_mask = 0xffffffff ^ self.addr_mask
    print "sparse memory size %x addr mask %x block mask %x" \
          % ( self.block_size, self.addr_mask, self.block_mask )
    #blocks     = []
    self.block_dict = {}
    self.debug = Debug()

  def add_block( self, block_addr ):
    #print "adding block: %x" % block_addr
    self.block_dict[ block_addr ] = self.BlockMemory( size=self.block_size,
                                                      nbits=self.nbits )

  @elidable
  def get_block_mem( self, block_addr ):
    #block_idx  = block_dict[ 
    if block_addr not in self.block_dict:
      self.add_block( block_addr )
    block_mem = self.block_dict[ block_addr ]
    return block_mem

  @elidable
  def iread( self, start_addr, num_bytes ):
    start_addr = hint( start_addr, promote=True )
    num_bytes  = hint( num_bytes,  promote=True )

    block_addr = self.block_mask & start_addr
    block_mem = self.get_block_mem( block_addr )
    return block_mem.iread( start_addr & self.addr_mask, num_bytes )

  def read( self, start_addr, num_bytes ):
    if self.debug.enabled( "mem" ):
      print ':: RD.MEM[%s] = ' % pad_hex( start_addr ),
    block_addr = self.block_mask & start_addr
    block_addr = hint( block_addr, promote=True )
    block_mem = self.get_block_mem( block_addr )
    value = block_mem.read( start_addr & self.addr_mask, num_bytes )
    if self.debug.enabled( "mem" ):
      print '%s' % pad_hex( value ),
    return value

  def write( self, start_addr, num_bytes, value ):
    if self.debug.enabled( "mem" ):
      print ':: WR.MEM[%s] = %s' % ( pad_hex( start_addr ),
                                     pad_hex( value ) ),
    block_addr = self.block_mask & start_addr
    block_addr = hint( block_addr, promote=True )
    block_mem = self.get_block_mem( block_addr )
    block_mem.write( start_addr & self.addr_mask, num_bytes, value )




