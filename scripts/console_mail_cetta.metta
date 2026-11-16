!(import! &self ../src/console/mail_cetta.metta)
!(let $arg (system:arg 0) (cm:serve (sf:native-object (cli:text $arg))))
