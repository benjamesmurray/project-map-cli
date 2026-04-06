; Package and Imports
(package_header (identifier) @package.name)
(import_header (identifier) @import.name)

; Classes, Objects, Interfaces, and Enums
(class_declaration) @class
(class_declaration (type_identifier) @class.name)

(object_declaration) @class
(object_declaration (type_identifier) @class.name)

(interface_declaration) @class
(interface_declaration (type_identifier) @class.name)

(enum_class_declaration) @class
(enum_class_declaration (type_identifier) @class.name)

; Companion Object
(companion_object) @class
(companion_object (type_identifier)? @class.name)

; Functions and Methods
(function_declaration) @function
(function_declaration (simple_identifier) @function.name)

; Calls
(call_expression) @call
(call_expression (simple_identifier) @call.name)
(call_expression 
  (navigation_expression 
    (navigation_suffix (simple_identifier) @call.name)))
(call_expression
  (navigation_expression
    ((simple_identifier) @call.receiver)
    (navigation_suffix (simple_identifier) @call.name)))
