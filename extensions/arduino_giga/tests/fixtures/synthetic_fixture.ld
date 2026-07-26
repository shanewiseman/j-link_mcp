SECTIONS
{
  . = 0x08000000;
  .text : { *(.text*) }
  .manifest : { *(.manifest) }
  . = 0x24000000;
  .ram : { *(.ram) *(.rtt) }
}
