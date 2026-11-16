!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

!(println! (gateway:boot-probe))
!(mettaclaw)
