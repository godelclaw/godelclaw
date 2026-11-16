!(import! &self ../src/execution_gateway/read_tools_cetta.metta)
!(if (!= (system:argc) 2) (system:exit 2)
   (let* (($name (system:arg 0)) ($path (system:arg 1)))
     (ngr:main (cli:text $name) (cli:text $path))))
